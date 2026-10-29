"""Chat use case: authorise → rate-limit → answer → persist one audited exchange.

Every question that passes validation, authorisation and rate limiting produces exactly one
exchange row, whatever the outcome (answered, refused, unsupported or failed).
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.application.rag.service import NOTICE_FAILED, AnswerService
from app.domain.models.answers import (
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    Citation,
    VerifiedClaim,
)
from app.domain.models.identity import Principal
from app.infrastructure.database.models import Exchange
from app.infrastructure.repositories.chat import ChatRepository

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 80


class ConversationNotFoundError(Exception):
    """Unknown conversation *or* owned by someone else (indistinguishable on purpose)."""


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


def conversation_title(question: str) -> str:
    if len(question) <= TITLE_MAX_CHARS:
        return question
    cut = question[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{cut}…"


def _claim_json(claim: VerifiedClaim) -> dict[str, Any]:
    return {"text": claim.text, "citations": list(claim.citation_numbers)}


def _citation_json(citation: Citation) -> dict[str, Any]:
    data = asdict(citation)
    data["chunk_id"] = str(citation.chunk_id)
    data["quotes"] = list(citation.quotes)
    return data


class ChatService:
    def __init__(
        self,
        session: Session,
        answer_service: AnswerService,
        *,
        per_minute: int,
        per_hour: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._repo = ChatRepository(session)
        self._answers = answer_service
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._clock = clock

    def ask(
        self,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None,
        request_id: str | None,
    ) -> Exchange:
        conversation = None
        if conversation_id is not None:
            conversation = self._repo.get_owned_conversation(conversation_id, principal.user_id)
            if conversation is None:
                raise ConversationNotFoundError
        self._enforce_rate_limit(principal.user_id)

        started = time.perf_counter()
        try:
            answer = self._answers.answer(question)
        except Exception:
            # AnswerService handles expected failures; this is a bug guard so that even an
            # unexpected crash is still recorded as a failed exchange.
            logger.exception("answer service crashed")
            answer = AssistantAnswer(
                status=AnswerStatus.FAILED, reason=AnswerReason.MODEL_ERROR, notice=NOTICE_FAILED
            )
        latency_ms = round((time.perf_counter() - started) * 1000)

        # Single short write transaction: user mirror, conversation, exchange.
        self._repo.upsert_user(principal)
        if conversation is None:
            conversation = self._repo.create_conversation(
                principal.user_id, conversation_title(question)
            )
        exchange = self._repo.add_exchange(
            conversation,
            user_id=principal.user_id,
            question=question,
            answer_text=answer.render_text(),
            answer_payload={
                "notice": answer.notice,
                "explanation": [_claim_json(c) for c in answer.explanation],
                "key_considerations": [_claim_json(c) for c in answer.key_considerations],
                "disclaimer": answer.disclaimer,
            },
            sources=[_citation_json(c) for c in answer.citations],
            status=answer.status.value,
            reason=answer.reason.value if answer.reason else None,
            model_id=answer.model_id,
            input_tokens=answer.usage.input_tokens if answer.usage else None,
            output_tokens=answer.usage.output_tokens if answer.usage else None,
            latency_ms=latency_ms,
            retrieved_passages=answer.retrieved_passages,
            dropped_claims=answer.dropped_claims,
            request_id=request_id,
        )
        self._session.commit()
        logger.info(
            "exchange recorded",
            extra={
                "exchange_id": str(exchange.id),
                "status": exchange.status,
                "reason": exchange.reason,
                "latency_ms": latency_ms,
            },
        )
        return exchange

    def _enforce_rate_limit(self, user_id: str) -> None:
        now = self._clock()
        if (
            self._repo.count_exchanges_since(user_id, now - timedelta(minutes=1))
            >= self._per_minute
        ):
            raise RateLimitExceededError(60)
        if self._repo.count_exchanges_since(user_id, now - timedelta(hours=1)) >= self._per_hour:
            raise RateLimitExceededError(3600)
