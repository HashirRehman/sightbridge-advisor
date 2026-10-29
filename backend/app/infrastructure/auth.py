"""Bearer-token verification. Every claim used for authorisation comes from a token whose
signature, issuer, audience, expiry and type have been checked here."""

from typing import Any, Protocol

import jwt

from app.domain.models.identity import Principal

LOCAL_ISSUER = "sightbridge-local"


class InvalidTokenError(Exception):
    pass


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def _principal(claims: dict[str, Any], admin_group: str) -> Principal:
    sub, email = claims.get("sub"), claims.get("email")
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
        raise InvalidTokenError("token missing sub/email")
    groups = claims.get("cognito:groups") or []
    return Principal(
        user_id=sub,
        email=email.lower(),
        is_admin=isinstance(groups, list) and admin_group in groups,
    )


class CognitoTokenVerifier:
    """Verifies Cognito ID tokens (RS256) against the user pool's JWKS."""

    def __init__(
        self, issuer: str, client_id: str, admin_group: str, keys: SigningKeySource | None = None
    ) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._admin_group = admin_group
        # PyJWKClient caches keys in memory, so the JWKS is fetched once per Lambda instance.
        self._keys = keys or jwt.PyJWKClient(
            f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=5
        )

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._keys.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],  # pinned: rejects "none" and HS256 key-confusion
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]},
                leeway=30,
            )
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise InvalidTokenError(type(exc).__name__) from exc
        if claims.get("token_use") != "id":
            raise InvalidTokenError("not an ID token")
        return _principal(claims, self._admin_group)


class LocalTokenVerifier:
    """HS256 tokens minted by the local Next.js dev server. Refused in production by config."""

    def __init__(self, secret: str, admin_group: str) -> None:
        if len(secret) < 32:
            raise ValueError("LOCAL_AUTH_SECRET must be at least 32 characters")
        self._secret = secret
        self._admin_group = admin_group

    def verify(self, token: str) -> Principal:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=LOCAL_ISSUER,
                issuer=LOCAL_ISSUER,
                options={"require": ["exp", "iat", "sub", "token_use"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(type(exc).__name__) from exc
        if claims.get("token_use") != "id":
            raise InvalidTokenError("not an ID token")
        return _principal(claims, self._admin_group)
