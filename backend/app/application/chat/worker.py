"""Trusted asynchronous entry. Claim once, verify ownership/fingerprint, then process.

Question text travels in the Lambda event, never in the activity table or logs.
The HTTP gateway cannot select this entry point with a request body.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.chat.service import ChatService
from app.core.config import get_settings
from app.core.logging import request_id_var
from app.domain.models.identity import Principal
from app.domain.policies.safety import question_for_storage
from app.infrastructure.database.models import User
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.factory import build_answer_service, build_embedder, build_generator
from app.infrastructure.repositories.activity import ActivityRepository
from app.schemas.chat import ChatRequest


def process_answer_event(event: dict[str, Any]) -> dict[str, str]:
    if event.get("source") != "sightbridge.answer-worker" or "requestContext" in event:
        return {"status": "ignored"}
    try:
        request = ChatRequest.model_validate(
            {
                "question": event.get("question"),
                "conversation_id": event.get("conversation_id"),
                "activity_id": event.get("activity_id"),
            }
        )
        activity_id = uuid.UUID(str(event.get("activity_id")))
        user_id = str(event.get("user_id"))
    except (ValueError, TypeError):
        return {"status": "ignored"}
    with get_session_factory()() as session:
        repo = ActivityRepository(session)
        activity = repo.get(activity_id, user_id)
        if (
            activity is None
            or activity.finished_at is not None
            or activity.created_at < datetime.now(UTC) - timedelta(seconds=130)
        ):
            return {"status": "ignored"}
        fingerprint = hashlib.sha256(
            f"{request.conversation_id}:{question_for_storage(request.question)}".encode()
        ).hexdigest()
        if activity.fingerprint != fingerprint:
            return {"status": "ignored"}
        principal = session.get(User, user_id)
        if principal is None or not repo.claim(activity_id):
            return {"status": "ignored"}
        try:
            settings = get_settings()
            answers = build_answer_service(
                session,
                settings,
                build_embedder(settings),
                build_generator(
                    settings,
                    primary_timeout=settings.worker_generation_timeout_seconds,
                    fallback_timeout=settings.worker_answer_timeout_seconds,
                ),
                timeout_seconds=settings.worker_answer_timeout_seconds,
            )
            service = ChatService(
                session,
                answers,
                per_minute=settings.rate_limit_per_minute,
                per_hour=settings.rate_limit_per_hour,
            )
            token = request_id_var.set(str(event.get("request_id", activity_id.hex))[:64])
            try:
                service.process_reserved(
                    Principal(principal.id, principal.email, principal.is_admin),
                    request.question,
                    request.conversation_id,
                    request_id_var.get(),
                    activity,
                )
            finally:
                request_id_var.reset(token)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "answer worker failed",
                extra={"error_type": type(exc).__name__, "activity_id": str(activity_id)},
            )
            session.rollback()
            activity.finished_at = datetime.now(UTC)
            session.commit()
            return {"status": "failed"}
    return {"status": "completed"}
