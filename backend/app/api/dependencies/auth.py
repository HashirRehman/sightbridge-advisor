from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.domain.models.identity import Principal
from app.infrastructure.auth import (
    CognitoTokenVerifier,
    InvalidTokenError,
    LocalTokenVerifier,
    TokenVerifier,
)


@lru_cache
def get_token_verifier() -> TokenVerifier:
    settings = get_settings()
    if settings.auth_provider == "local":
        return LocalTokenVerifier(
            settings.local_auth_secret.get_secret_value(), settings.admin_group_name
        )
    return CognitoTokenVerifier(
        settings.cognito_issuer, settings.cognito_app_client_id, settings.admin_group_name
    )


_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Authentication required.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_principal(
    request: Request, verifier: Annotated[TokenVerifier, Depends(get_token_verifier)]
) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _UNAUTHENTICATED
    try:
        return verifier.verify(token)
    except InvalidTokenError:
        raise _UNAUTHENTICATED from None


def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return principal


CurrentUser = Annotated[Principal, Depends(get_principal)]
AdminUser = Annotated[Principal, Depends(require_admin)]
