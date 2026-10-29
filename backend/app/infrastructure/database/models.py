"""ORM models — the single source of truth for the database schema.

Change a model, then generate a reviewed migration:
    uv run alembic revision --autogenerate -m "<change>"
"""

from app.infrastructure.database.base import Base

__all__ = ["Base"]
