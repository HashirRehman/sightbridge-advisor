import os

# Must be set before any app module reads settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["LLM_PROVIDER"] = "local"
# Tests must never reach a real AWS account: dummy credentials, no profile, no IMDS lookup.
os.environ.pop("AWS_PROFILE", None)
os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
# Always the dedicated test database — integration tests TRUNCATE tables.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://sightbridge:sightbridge@localhost:5433/sightbridge_test",
)
if not os.environ["DATABASE_URL"].rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
    raise RuntimeError("Refusing to run tests against a database not named *_test")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[None]:
    """Bring the test database to the latest Alembic revision (the real migration path)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    yield
