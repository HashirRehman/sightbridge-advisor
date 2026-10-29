import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.auth import InvalidTokenError, LocalTokenVerifier
from tests.auth_helpers import CLIENT_ID, ISSUER, OTHER_KEY, SIGNING_KEY, make_token, verifier


def test_valid_id_token_yields_principal_from_claims_only() -> None:
    principal = verifier().verify(make_token(email="Advisor1@Example.com"))
    assert (principal.user_id, principal.email, principal.is_admin) == (
        "advisor-1",
        "advisor1@example.com",
        False,
    )


def test_admin_role_comes_from_cognito_group() -> None:
    assert verifier().verify(make_token(groups=("admin",))).is_admin is True
    assert verifier().verify(make_token(groups=("administrators",))).is_admin is False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _hs256_signed_with_public_key() -> str:
    """Algorithm-confusion attack: HMAC-sign with the (public) RSA key as the secret."""
    public_pem = SIGNING_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": "attacker",
                "email": "attacker@example.com",
                "cognito:groups": ["admin"],
                "token_use": "id",
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "iat": now,
                "exp": now + 3600,
            }
        ).encode()
    )
    signature = hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(make_token(exp=int(time.time()) - 120), id="expired"),
        pytest.param(make_token(aud="some-other-client"), id="wrong-audience"),
        pytest.param(make_token(iss="https://evil.example.com"), id="wrong-issuer"),
        pytest.param(make_token(token_use="access"), id="access-token-not-id-token"),
        pytest.param(make_token(key=OTHER_KEY), id="signed-by-another-key"),
        pytest.param(make_token(email=None), id="missing-email"),
        pytest.param(
            jwt.encode({"sub": "x", "token_use": "id"}, key="", algorithm="none"), id="alg-none"
        ),
        pytest.param(_hs256_signed_with_public_key(), id="hs256-key-confusion"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_untrusted_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        verifier().verify(token)


def test_local_verifier_requires_strong_secret_and_valid_tokens() -> None:
    with pytest.raises(ValueError, match="32"):
        LocalTokenVerifier("short", "admin")
    secret = "x" * 32
    token = jwt.encode(
        {
            "sub": "u1",
            "email": "u1@example.com",
            "token_use": "id",
            "iss": "sightbridge-local",
            "aud": "sightbridge-local",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        secret,
        algorithm="HS256",
    )
    assert LocalTokenVerifier(secret, "admin").verify(token).user_id == "u1"
    with pytest.raises(InvalidTokenError):
        LocalTokenVerifier("y" * 32, "admin").verify(token)


def test_production_refuses_local_auth() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_provider="local")
