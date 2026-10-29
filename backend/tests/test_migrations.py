import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.infrastructure.database.session import get_engine

pytestmark = pytest.mark.integration


def _config() -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["configure_logger"] = False
    return cfg


def test_migrations_upgrade_downgrade_roundtrip() -> None:
    cfg = _config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    head = ScriptDirectory.from_config(cfg).get_current_head()
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
        assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()


def test_single_migration_head() -> None:
    assert len(ScriptDirectory.from_config(_config()).get_heads()) == 1


def test_orm_models_match_migrations() -> None:
    """Fails if someone changes a model without generating a migration."""
    cfg = _config()
    command.upgrade(cfg, "head")
    command.check(cfg)
