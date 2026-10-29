import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.release import release_source_id
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


@router.get("/api/release")
def release(response: Response) -> dict[str, str | None]:
    source_id = release_source_id()
    response.headers["Cache-Control"] = "no-store"
    if source_id is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"source_id": source_id}
