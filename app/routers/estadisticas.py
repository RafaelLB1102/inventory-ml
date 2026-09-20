"""Metricas de uso del servicio e informacion del modelo."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.modelo import obtener_clasificador
from app.core.security import usuario_actual
from app.schemas.predicciones import ConteoClase, Estadisticas, InfoModelo

router = APIRouter(tags=["estadisticas"])


@router.get("/stats", response_model=Estadisticas, summary="Metricas de uso")
def estadisticas(db: Session = Depends(get_db), _=Depends(usuario_actual)):
    """
    Resume el uso del clasificador: volumen, confianza media, latencia y
    reparto por categoria.

    Las metricas son globales, no por usuario: describen el comportamiento del
    servicio, que es lo que interesa vigilar en el panel de monitoreo.
    """
    from app.models.predicciones import Prediccion

    ahora = datetime.now(timezone.utc)
    base = db.query(Prediccion)

    total = base.count()
    if total == 0:
        return Estadisticas(
            total_predicciones=0, predicciones_24h=0, predicciones_7d=0,
            confianza_media=0.0, latencia_media_ms=0.0,
            tasa_incertidumbre=0.0, por_clase=[],
        )

    agregados = db.query(
        func.avg(Prediccion.confianza), func.avg(Prediccion.latencia_ms)
    ).one()
    inciertas = base.filter(Prediccion.incierta.is_(True)).count()

    por_clase = (
        db.query(
            Prediccion.clase_predicha,
            func.count(Prediccion.id),
            func.avg(Prediccion.confianza),
        )
        .group_by(Prediccion.clase_predicha)
        .order_by(func.count(Prediccion.id).desc())
        .all()
    )

    return Estadisticas(
        total_predicciones=total,
        predicciones_24h=base.filter(Prediccion.creado_en >= ahora - timedelta(days=1)).count(),
        predicciones_7d=base.filter(Prediccion.creado_en >= ahora - timedelta(days=7)).count(),
        confianza_media=round(float(agregados[0] or 0), 4),
        latencia_media_ms=round(float(agregados[1] or 0), 2),
        tasa_incertidumbre=round(inciertas / total, 4),
        por_clase=[
            ConteoClase(clase=c, total=n, confianza_media=round(float(conf), 4))
            for c, n, conf in por_clase
        ],
    )


@router.get("/model", response_model=InfoModelo, summary="Informacion del modelo")
def info_modelo(_=Depends(usuario_actual)):
    """
    Expone las metricas obtenidas durante la evaluacion sobre el conjunto de
    prueba. Permite al frontend mostrar el rendimiento real del modelo en
    lugar de cifras escritas a mano que podrian quedar desactualizadas.
    """
    c = obtener_clasificador()
    m = c.metadatos
    return InfoModelo(
        arquitectura=m["arquitectura"],
        clases=m["clases"],
        exactitud_prueba=m["metricas"]["exactitud_prueba"],
        f1_macro=m["metricas"]["f1_macro"],
        umbral_confianza=settings.UMBRAL_CONFIANZA,
        metricas_por_clase=m["metricas"]["por_clase"],
    )
