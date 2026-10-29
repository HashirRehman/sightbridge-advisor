"""Alembic environment: the schema is defined by the SQLAlchemy ORM models in app/."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.infrastructure.database.models
from app.core.config import get_settings
from app.infrastructure.database.base import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# Escape % for configparser interpolation (passwords may contain it).
config.set_main_option(
    "sqlalchemy.url", get_settings().database_url.get_secret_value().replace("%", "%%")
)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
