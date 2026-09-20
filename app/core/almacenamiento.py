"""
Guarda las imagenes subidas en S3 y genera enlaces temporales para verlas.

El bucket es privado. El frontend nunca recibe una URL permanente sino una
firmada que caduca, de modo que un enlace filtrado deja de servir solo.
"""
import logging
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

log = logging.getLogger(__name__)

_s3 = None


def cliente():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=settings.AWS_REGION)
    return _s3


def disponible() -> bool:
    return bool(settings.BUCKET_IMAGENES)


def guardar(datos: bytes, tipo_mime: str, usuario_id) -> str | None:
    """Sube la imagen y devuelve su clave. None si el almacenamiento no esta configurado."""
    if not disponible():
        return None

    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(tipo_mime, "bin")
    hoy = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    clave = f"predicciones/{hoy}/{usuario_id}/{uuid.uuid4().hex}.{extension}"
    try:
        cliente().put_object(
            Bucket=settings.BUCKET_IMAGENES, Key=clave, Body=datos,
            ContentType=tipo_mime, ServerSideEncryption="AES256",
        )
        return clave
    except (BotoCoreError, ClientError) as exc:
        # Una imagen no almacenada no debe impedir devolver la prediccion.
        log.warning("No se pudo guardar la imagen en S3: %s", exc)
        return None


def url_temporal(clave: str | None) -> str | None:
    if not clave or not disponible():
        return None
    try:
        return cliente().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.BUCKET_IMAGENES, "Key": clave},
            ExpiresIn=settings.URL_FIRMADA_SEGUNDOS,
        )
    except (BotoCoreError, ClientError) as exc:
        log.warning("No se pudo firmar la URL de %s: %s", clave, exc)
        return None
