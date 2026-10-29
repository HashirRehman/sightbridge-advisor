import time
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.infrastructure.auth import CognitoTokenVerifier

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool"
CLIENT_ID = "test-app-client"

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticKeys:
    """Replaces the JWKS fetch with the test pool's public key."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=SIGNING_KEY.public_key())


def verifier() -> CognitoTokenVerifier:
    return CognitoTokenVerifier(ISSUER, CLIENT_ID, "admin", keys=StaticKeys())


def make_token(
    sub: str = "advisor-1",
    email: str | None = "advisor1@example.com",
    groups: tuple[str, ...] = (),
    key: Any = SIGNING_KEY,
    algorithm: str = "RS256",
    **overrides: Any,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "cognito:groups": list(groups),
        "token_use": "id",
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=algorithm)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
