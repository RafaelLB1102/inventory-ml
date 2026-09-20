"""
Validacion del token emitido por inventory-be.

Este servicio no gestiona usuarios ni contrasenas: solo comprueba que el token
venga firmado con el secreto compartido y no haya expirado. El identificador
del usuario viaja en el claim "sub".

Consecuencia asumida: si un usuario se elimina en inventory-be, su token sigue
siendo valido aqui hasta que expire (60 minutos). Se acepta a cambio de no
acoplar este servicio al esquema de usuarios del otro.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

esquema = HTTPBearer(auto_error=False)


def usuario_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(esquema),
) -> UUID:
    no_autorizado = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales invalidas o ausentes",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credenciales is None:
        raise no_autorizado
    try:
        carga = jwt.decode(
            credenciales.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        sub = carga.get("sub")
        if sub is None:
            raise no_autorizado
        return UUID(sub)
    except (JWTError, ValueError, AttributeError):
        raise no_autorizado
