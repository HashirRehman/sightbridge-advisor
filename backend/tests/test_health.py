from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.infrastructure.database.session import get_db_session


@pytest.mark.integration
def test_health_reports_ok_with_database(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
    assert response.headers["X-Request-ID"]


def test_health_degrades_when_database_unreachable(client: TestClient) -> None:
    broken = MagicMock()
    broken.execute.side_effect = OperationalError("SELECT 1", {}, Exception("down"))

    def broken_session() -> Iterator[MagicMock]:
        yield broken

    client.app.dependency_overrides[get_db_session] = broken_session  # type: ignore[attr-defined]

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "version": "0.1.0", "database": "unavailable"}
