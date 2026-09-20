"""Endpoints de clasificación e historial."""
import json
import logging
import math
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core import almacenamiento
from app.core.config import settings
from app.core.database import get_db
from app.core.modelo import ErrorImagen, obtener_clasificador
from app.core.security import usuario_actual
from app.models.predicciones import Prediccion
from app.schemas.predicciones import ItemHistorial, PaginaHistorial, RespuestaPrediccion

log = logging.getLogger(__name__)
router = APIRouter(tags=["predicciones"])


@router.post(
    "/predict",
    response_model=RespuestaPrediccion,
    status_code=status.HTTP_201_CREATED,
    summary="Clasificar una imagen",
    responses={
        400: {"description": "El archivo no es una imagen válida o está corrupta"},
        401: {"description": "Token ausente o inválido"},
        413: {"description": "La imagen supera el tamaño máximo permitido"},
        415: {"description": "Tipo de archivo no soportado"},
        503: {"description": "El modelo no está disponible"},
    },
)
async def clasificar(
    imagen: UploadFile = File(..., description="Imagen JPEG, PNG o WebP"),
    db: Session = Depends(get_db),
    usuario_id: UUID = Depends(usuario_actual),
):
    """
    Recibe una imagen y devuelve la categoría de producto predicha junto con
    la probabilidad de cada una de las seis clases.

    Cuando la confianza no supera el umbral configurado, la respuesta se marca
    como **incierta**. Durante la evaluación la confianza media fue de 0,88 en
    los aciertos y 0,59 en los errores, por lo que ese indicador permite
    distinguir las predicciones poco fiables en lugar de presentarlas con la
    misma autoridad que las demás.
    """
    clasificador = obtener_clasificador()
    if clasificador is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El modelo no está cargado; el servicio no puede clasificar en este momento",
        )

    # --- Validación del tipo declarado -------------------------------------
    tipo = (imagen.content_type or "").lower()
    if tipo not in settings.tipos_permitidos:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Tipo '{tipo or 'desconocido'}' no soportado. "
            f"Se admiten: {', '.join(sorted(settings.tipos_permitidos))}",
        )

    # --- Validación del tamaño ---------------------------------------------
    # Se lee completo porque el modelo necesita la imagen entera, pero se
    # comprueba el límite antes de procesarla para no gastar CPU en un archivo
    # que se va a rechazar.
    datos = await imagen.read()
    if len(datos) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El archivo está vacío")
    if len(datos) > settings.tamano_maximo_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"La imagen pesa {len(datos)/1048576:.1f} MB y el máximo es "
            f"{settings.TAMANO_MAXIMO_MB} MB",
        )

    # --- Inferencia ---------------------------------------------------------
    try:
        resultado = clasificador.predecir(datos)
    except ErrorImagen as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception:
        log.exception("Fallo inesperado durante la inferencia")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "No se pudo procesar la imagen",
        )

    # El almacenamiento es accesorio: si S3 falla, la predicción igual se
    # devuelve y se registra, solo queda sin imagen asociada.
    clave = almacenamiento.guardar(datos, tipo, usuario_id)

    registro = Prediccion(
        usuario_id=usuario_id,
        nombre_archivo=(imagen.filename or "sin-nombre")[:255],
        clave_s3=clave,
        tipo_mime=tipo,
        tamano_bytes=len(datos),
        ancho=resultado["dimensiones"][0],
        alto=resultado["dimensiones"][1],
        clase_predicha=resultado["clase"],
        confianza=resultado["confianza"],
        incierta=resultado["incierta"],
        probabilidades=resultado["probabilidades"],
        latencia_ms=resultado["latencia_ms"],
    )
    db.add(registro)
    db.commit()
    db.refresh(registro)

    # Log estructurado: CloudWatch Logs Insights puede consultarlo y alimentar
    # el panel de monitoreo sin necesidad de métricas personalizadas de pago.
    log.info(json.dumps({
        "evento": "prediccion",
        "clase": resultado["clase"],
        "confianza": resultado["confianza"],
        "incierta": resultado["incierta"],
        "latencia_ms": resultado["latencia_ms"],
        "bytes": len(datos),
    }))

    return RespuestaPrediccion(
        id=registro.id,
        clase=resultado["clase"],
        confianza=resultado["confianza"],
        incierta=resultado["incierta"],
        probabilidades=resultado["probabilidades"],
        latencia_ms=resultado["latencia_ms"],
        creado_en=registro.creado_en,
    )


@router.get(
    "/history",
    response_model=PaginaHistorial,
    summary="Historial de predicciones del usuario",
)
def historial(
    pagina: int = Query(1, ge=1, description="Número de página, empezando en 1"),
    por_pagina: int = Query(20, ge=1, le=100, description="Resultados por página"),
    clase: str | None = Query(None, description="Filtrar por categoría predicha"),
    db: Session = Depends(get_db),
    usuario_id: UUID = Depends(usuario_actual),
):
    """
    Devuelve las clasificaciones anteriores del usuario autenticado, de la más
    reciente a la más antigua.

    Cada elemento incluye un enlace temporal a la imagen original. El bucket
    es privado, de modo que el enlace caduca y no queda accesible de forma
    permanente.
    """
    consulta = db.query(Prediccion).filter(Prediccion.usuario_id == usuario_id)
    if clase:
        consulta = consulta.filter(Prediccion.clase_predicha == clase)

    total = consulta.count()
    filas = (
        consulta.order_by(desc(Prediccion.creado_en))
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
        .all()
    )

    items = []
    for f in filas:
        item = ItemHistorial.model_validate(f)
        item.url_imagen = almacenamiento.url_temporal(f.clave_s3)
        items.append(item)

    return PaginaHistorial(
        total=total, pagina=pagina, por_pagina=por_pagina, items=items
    )


@router.delete(
    "/history/{prediccion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una predicción del historial",
    responses={404: {"description": "La predicción no existe o no pertenece al usuario"}},
)
def eliminar(
    prediccion_id: UUID,
    db: Session = Depends(get_db),
    usuario_id: UUID = Depends(usuario_actual),
):
    """Elimina un registro propio. La condición sobre `usuario_id` evita que
    alguien borre predicciones ajenas conociendo su identificador."""
    registro = (
        db.query(Prediccion)
        .filter(Prediccion.id == prediccion_id, Prediccion.usuario_id == usuario_id)
        .first()
    )
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Predicción no encontrada")
    db.delete(registro)
    db.commit()
