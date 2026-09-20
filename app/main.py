"""
Servicio de clasificación de imágenes de productos de inventario.

Se despliega junto a inventory-be detrás del mismo balanceador. CloudFront
enruta las peticiones que empiezan por /ml hacia este servicio, y el resto
hacia el frontend o la API de inventario.
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal, crear_tablas
from app.core.modelo import cargar_clasificador, obtener_clasificador
from app.routers import estadisticas, predicciones

# Salida sin buffer y en una sola línea por evento: CloudWatch Logs trata cada
# línea como un registro, y un traceback multilínea se fragmenta en varios.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("inventory-ml")

API_PREFIX = "/ml"


@asynccontextmanager
async def ciclo_vida(app: FastAPI):
    """El modelo se carga una vez al arrancar, no en cada petición."""
    crear_tablas()
    try:
        cargar_clasificador()
    except Exception:
        # El contenedor sigue vivo para que /ml/health informe el problema:
        # morir en silencio dejaría al balanceador sin saber qué ocurrió.
        log.exception("No se pudo cargar el modelo")
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description=(
        "Clasifica fotografías de productos en seis categorías: impresora, "
        "laptop, monitor, mouse, silla y teclado.\n\n"
        "Todos los endpoints requieren el token JWT que emite el servicio de "
        "inventario en `/api/auth/login`."
    ),
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=None,
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=ciclo_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origenes,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(predicciones.router, prefix=API_PREFIX)
app.include_router(estadisticas.router, prefix=API_PREFIX)


@app.get("/health", tags=["salud"], summary="Sonda de salud")
def salud():
    """
    Comprueba que el modelo esté cargado y la base de datos accesible.

    Sin prefijo porque la consulta el balanceador directamente contra el
    contenedor, sin pasar por CloudFront.
    """
    modelo_ok = obtener_clasificador() is not None
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        bd_ok = True
    except Exception:
        bd_ok = False

    estado = "ok" if (modelo_ok and bd_ok) else "degradado"
    cuerpo = {
        "status": estado,
        "modelo": "cargado" if modelo_ok else "no disponible",
        "database": "reachable" if bd_ok else "unreachable",
    }
    # 503 hace que el balanceador retire la tarea del grupo de destino en
    # lugar de seguir enviándole tráfico que va a fallar.
    return JSONResponse(cuerpo, status_code=200 if estado == "ok" else 503)


@app.get("/", tags=["salud"], include_in_schema=False)
def raiz():
    return {"servicio": settings.APP_NAME, "version": settings.VERSION, "docs": f"{API_PREFIX}/docs"}
