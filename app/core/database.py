from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# pool_pre_ping detecta conexiones muertas: RDS cierra las inactivas y sin
# esto la primera peticion tras un rato de inactividad fallaria.
engine = create_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def crear_tablas():
    from app.models import predicciones  # noqa: F401  (registra el modelo)
    Base.metadata.create_all(bind=engine)
