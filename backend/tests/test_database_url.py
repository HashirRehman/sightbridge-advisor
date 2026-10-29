from typing import Any

from app.core.config import Settings
from app.infrastructure.database.session import resolve_database_url


class FakeSsm:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {"Parameter": {"Value": "postgresql+psycopg://app:s3cret@db.example:5432/app"}}


def test_production_reads_database_url_from_encrypted_ssm_parameter() -> None:
    ssm = FakeSsm()
    settings = Settings(database_url_ssm_parameter="/sightbridge/prod/database-url")

    url = resolve_database_url(settings, ssm)

    assert url.endswith("@db.example:5432/app")
    assert ssm.requests == [{"Name": "/sightbridge/prod/database-url", "WithDecryption": True}]


def test_local_uses_database_url_setting_directly() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:p@localhost:5433/x")
    assert (
        resolve_database_url(settings, ssm_client=object())
        == "postgresql+psycopg://u:p@localhost:5433/x"
    )
