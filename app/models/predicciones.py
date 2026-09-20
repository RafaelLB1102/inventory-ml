from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
import uuid

from app.core.database import Base


class Prediccion(Base):
    """
    Historial de clasificaciones. Vive en la misma base que el inventario pero
    en su propia tabla: los servicios comparten instancia por costo, no esquema.
    """
    __tablename__ = "predicciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Se guarda el id del usuario sin clave foranea: este servicio no gestiona
    # usuarios y una FK lo ataria al esquema de inventory-be.
    usuario_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    nombre_archivo = Column(String, nullable=False)
    clave_s3 = Column(String, nullable=True)
    tipo_mime = Column(String, nullable=False)
    tamano_bytes = Column(Integer, nullable=False)
    ancho = Column(Integer, nullable=True)
    alto = Column(Integer, nullable=True)

    clase_predicha = Column(String, nullable=False, index=True)
    confianza = Column(Float, nullable=False)
    incierta = Column(Boolean, nullable=False, default=False)
    probabilidades = Column(JSONB, nullable=False)

    latencia_ms = Column(Float, nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=func.now(), index=True)
