import os
from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.bedrock.client import (
    build_bedrock_runtime_client,
    resolve_bedrock_api_key,
)


class FakeSsm:
    def __init__(self, value: str = "test-bearer-key") -> None:
        self.value = value
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Parameter": {"Value": self.value}}


def test_bedrock_ssm_key_is_decrypted_without_exposing_it_in_settings() -> None:
    ssm = FakeSsm()
    settings = Settings(_env_file=None, bedrock_api_key_ssm_parameter="/app/bedrock-api-key")
    assert resolve_bedrock_api_key(settings, ssm) == ssm.value
    assert ssm.calls == [{"Name": "/app/bedrock-api-key", "WithDecryption": True}]
    assert ssm.value not in repr(settings)


def test_direct_bearer_key_is_secret_and_does_not_call_ssm() -> None:
    settings = Settings(_env_file=None, aws_bearer_token_bedrock="test-bearer-key")
    assert resolve_bedrock_api_key(settings, object()) == "test-bearer-key"
    assert "test-bearer-key" not in repr(settings)


def test_empty_ssm_key_fails_instead_of_using_other_credentials() -> None:
    settings = Settings(_env_file=None, bedrock_api_key_ssm_parameter="/app/key")
    with pytest.raises(ValueError, match="empty"):
        resolve_bedrock_api_key(settings, FakeSsm(""))


def test_client_uses_bedrock_token_in_sydney(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    calls: list[tuple[str, dict[str, Any]]] = []

    def client(service: str, **kwargs: Any) -> object:
        assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "test-bearer-key"
        calls.append((service, kwargs))
        return object()

    monkeypatch.setattr("app.infrastructure.bedrock.client.boto3.client", client)
    build_bedrock_runtime_client(
        Settings(_env_file=None, aws_bearer_token_bedrock="test-bearer-key")
    )
    assert calls[0][0] == "bedrock-runtime"
    assert calls[0][1]["region_name"] == "ap-southeast-2"
