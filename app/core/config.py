from pathlib import Path
from urllib.parse import quote_plus
import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    APP_NAME: str = "Inventory ML API"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # El token lo emite inventory-be; aqui solo se valida la firma.
    # Ambos servicios leen el mismo secreto de SSM Parameter Store.
    SECRET_KEY: str = os.getenv("SECRET_KEY", "default_secret_key")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")

    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173"
    )

    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "inventory")
    # quote_plus evita que una contrasena con #, % o + rompa la cadena
    DATABASE_URL: str = (
        f"postgresql://{quote_plus(POSTGRES_USER)}:{quote_plus(POSTGRES_PASSWORD)}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )

    # Almacenamiento de las imagenes subidas
    BUCKET_IMAGENES: str = os.getenv("BUCKET_IMAGENES", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    URL_FIRMADA_SEGUNDOS: int = int(os.getenv("URL_FIRMADA_SEGUNDOS", 900))

    # Modelo
    RUTA_MODELO: str = os.getenv("RUTA_MODELO", "modelo/clasificador.onnx")
    RUTA_METADATOS: str = os.getenv("RUTA_METADATOS", "modelo/metadatos.json")
    # Por debajo de este valor la prediccion se marca como incierta en lugar
    # de afirmarse: en evaluacion la confianza media de los aciertos fue 0,88
    # y la de los errores 0,59.
    UMBRAL_CONFIANZA: float = float(os.getenv("UMBRAL_CONFIANZA", 0.60))

    # Limites de la carga
    TAMANO_MAXIMO_MB: int = int(os.getenv("TAMANO_MAXIMO_MB", 5))
    LADO_MINIMO_PX: int = int(os.getenv("LADO_MINIMO_PX", 32))
    TIPOS_PERMITIDOS: str = "image/jpeg,image/png,image/webp"

    @property
    def origenes(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def tipos_permitidos(self) -> set[str]:
        return {t.strip() for t in self.TIPOS_PERMITIDOS.split(",")}

    @property
    def tamano_maximo_bytes(self) -> int:
        return self.TAMANO_MAXIMO_MB * 1024 * 1024

    class Config:
        env_file = ".env"


settings = Settings()

# En produccion no se admiten valores por defecto inseguros: es preferible
# que el contenedor no arranque a que quede validando tokens con una clave
# conocida o aceptando peticiones de cualquier origen.
if settings.ENVIRONMENT == "production":
    if settings.SECRET_KEY == "default_secret_key":
        raise RuntimeError("SECRET_KEY no configurada")
    if "*" in settings.origenes:
        raise RuntimeError("ALLOWED_ORIGINS no puede ser '*' en produccion")
    if not settings.BUCKET_IMAGENES:
        raise RuntimeError("BUCKET_IMAGENES no configurado")
