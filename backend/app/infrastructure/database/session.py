from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import boto3
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def resolve_database_url(settings: Settings, ssm_client: Any = None) -> str:
    if not settings.database_url_ssm_parameter:
        return settings.database_url.get_secret_value()
    ssm = ssm_client or boto3.client("ssm", region_name=settings.aws_region)
    response = ssm.get_parameter(Name=settings.database_url_ssm_parameter, WithDecryption=True)
    return str(response["Parameter"]["Value"])


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    # Small pool: on Lambda each instance serves one request at a time.
    return create_engine(
        resolve_database_url(settings),
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
