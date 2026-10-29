import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.infrastructure.database.session import get_db_session

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]


@router.get("/api/health", response_model=HealthResponse)
def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_db_session)],
) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
        database: Literal["ok", "unavailable"] = "ok"
    except Exception:
        logger.exception("health check database probe failed")
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=settings.app_version,
        database=database,
    )
