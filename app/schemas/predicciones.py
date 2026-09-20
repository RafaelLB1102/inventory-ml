from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RespuestaPrediccion(BaseModel):
    id: UUID
    clase: str = Field(..., description="Categoria con mayor probabilidad")
    confianza: float = Field(..., ge=0, le=1, description="Probabilidad de la clase predicha")
    incierta: bool = Field(..., description="La confianza no supera el umbral configurado")
    probabilidades: dict[str, float] = Field(..., description="Probabilidad de cada categoria")
    latencia_ms: float = Field(..., description="Tiempo de inferencia del modelo")
    creado_en: datetime

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "3f2b1c8e-5d4a-4b6c-9e8f-1a2b3c4d5e6f",
                "clase": "laptop",
                "confianza": 0.9412,
                "incierta": False,
                "probabilidades": {
                    "impresora": 0.0021, "laptop": 0.9412, "monitor": 0.0385,
                    "mouse": 0.0009, "silla": 0.0031, "teclado": 0.0142,
                },
                "latencia_ms": 2.31,
                "creado_en": "2026-09-20T15:04:05Z",
            }
        }
    }


class ItemHistorial(BaseModel):
    id: UUID
    nombre_archivo: str
    clase_predicha: str
    confianza: float
    incierta: bool
    latencia_ms: float
    creado_en: datetime
    url_imagen: str | None = Field(None, description="Enlace temporal a la imagen original")

    model_config = {"from_attributes": True}


class PaginaHistorial(BaseModel):
    total: int
    pagina: int
    por_pagina: int
    items: list[ItemHistorial]


class ConteoClase(BaseModel):
    clase: str
    total: int
    confianza_media: float


class Estadisticas(BaseModel):
    total_predicciones: int
    predicciones_24h: int
    predicciones_7d: int
    confianza_media: float
    latencia_media_ms: float
    tasa_incertidumbre: float = Field(..., description="Proporcion marcada como incierta")
    por_clase: list[ConteoClase]


class InfoModelo(BaseModel):
    arquitectura: str
    clases: list[str]
    exactitud_prueba: float
    f1_macro: float
    umbral_confianza: float
    metricas_por_clase: dict
