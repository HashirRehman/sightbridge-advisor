"""Chat use case: authorise → rate-limit → answer → persist one audited exchange.

Every question that passes validation, authorisation and rate limiting produces exactly one
exchange row, whatever the outcome (answered, refused, unsupported or failed).
"""

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.application.rag.activity import ActivityTracker, activity_step, track_activity
from app.application.rag.service import NOTICE_FAILED, NOTICE_PRIVACY, AnswerService
from app.domain.interfaces.dispatch import AnswerDispatcher
from app.domain.interfaces.safety import SafetyVerdict
from app.domain.models.answers import (
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    Citation,
    VerifiedClaim,
)
from app.domain.models.identity import Principal
from app.domain.policies.question_policy import PolicyOutcome, evaluate_question
from app.domain.policies.safety import local_safety_verdict, question_for_storage, sensitive_input
from app.infrastructure.database.models import AnswerActivity, Conversation, Exchange
from app.infrastructure.repositories.activity import ActivityRepository
from app.infrastructure.repositories.chat import ChatRepository

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 80


class ConversationNotFoundError(Exception):
    """Unknown conversation *or* owned by someone else (indistinguishable on purpose)."""


class ActivityExpiredError(Exception):
    pass


class ActivityConflictError(Exception):
    pass


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
        max_concurrent: int = 4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._repo = ChatRepository(session)
        self._answers = answer_service
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._max_concurrent = max_concurrent
        self._clock = clock

    def start(
        self,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None,
        request_id: str | None,
        activity_id: uuid.UUID | None,
        dispatcher: AnswerDispatcher,
    ) -> Exchange | AnswerActivity:
        activity_id = activity_id or uuid.uuid4()
        # Metadata replies and deterministic refusals need no queue or model call.
        decision = evaluate_question(question)
        if local_safety_verdict(question) is not SafetyVerdict.ALLOW or decision.outcome in (
            PolicyOutcome.CAPABILITIES,
            PolicyOutcome.PROMPT_INJECTION,
            PolicyOutcome.OUT_OF_SCOPE,
        ):
            return self.ask(principal, question, conversation_id, request_id, activity_id)
        _, activity = self.reserve(principal, question, conversation_id, activity_id)
        if activity.exchange_id is not None:
            cached = self._session.get(Exchange, activity.exchange_id)
            if cached is None or cached.user_id != principal.user_id:
                raise ConversationNotFoundError
            return cached
        self._repo.upsert_user(principal)
        self._session.commit()
        if activity.claimed_at is None and activity.finished_at is None:
            dispatcher.dispatch(
                {
                    "source": "sightbridge.answer-worker",
                    "activity_id": str(activity_id),
                    "user_id": principal.user_id,
                    "question": question,
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "request_id": request_id,
                }
            )
        return activity

    def ask(
        self,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None,
        request_id: str | None,
        activity_id: uuid.UUID | None = None,
    ) -> Exchange:
        activity_id = activity_id or uuid.uuid4()
        _, activity = self.reserve(principal, question, conversation_id, activity_id)
        if activity.exchange_id is not None:
            cached = self._session.get(Exchange, activity.exchange_id)
            if cached is None or cached.user_id != principal.user_id:
                raise ConversationNotFoundError
            return cached
        if not ActivityRepository(self._session).claim(activity.id):
            raise ActivityConflictError("This request is already being processed.")
        return self.process_reserved(principal, question, conversation_id, request_id, activity)

    def reserve(
        self,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None,
        activity_id: uuid.UUID,
    ) -> tuple[Conversation | None, AnswerActivity]:
        conversation = None
        if conversation_id is not None:
            conversation = self._repo.get_owned_conversation(conversation_id, principal.user_id)
            if conversation is None:
                raise ConversationNotFoundError
        activities = ActivityRepository(self._session)
        activities.lock_capacity()
        activities.lock_user(principal.user_id)
        stored_question = question_for_storage(question)
        fingerprint = hashlib.sha256(f"{conversation_id}:{stored_question}".encode()).hexdigest()
        existing = self._session.get(AnswerActivity, activity_id)
        if existing is not None:
            if existing.user_id != principal.user_id:
                raise ConversationNotFoundError
            if existing.fingerprint != fingerprint:
                raise ActivityConflictError(
                    "This request is already being processed or has different content."
                )
            if existing.exchange_id is None and (
                existing.finished_at is not None
                or existing.created_at < self._clock() - timedelta(seconds=130)
            ):
                existing.finished_at = self._clock()
                self._session.commit()
                raise ActivityExpiredError
            self._session.commit()
            return conversation, existing
        self._enforce_rate_limit(principal.user_id)
        if activities.active(principal.user_id, self._clock()) >= 2:
            raise RateLimitExceededError(5)
        if activities.active_total(self._clock()) >= self._max_concurrent:
            raise RateLimitExceededError(5)
        activity = activities.reserve(activity_id, principal.user_id, fingerprint)
        self._session.commit()
        return conversation, activity

    def process_reserved(
        self,
        principal: Principal,
        question: str,
        conversation_id: uuid.UUID | None,
        request_id: str | None,
        activity: AnswerActivity,
    ) -> Exchange:
        activity_id = activity.id
        conversation = (
            self._repo.get_owned_conversation(conversation_id, principal.user_id)
            if conversation_id
            else None
        )
        if conversation_id is not None and conversation is None:
            raise ConversationNotFoundError
        stored_question = question_for_storage(question)
        bind = self._session.get_bind()

        def publish(steps: list[dict[str, Any]]) -> None:
            with Session(bind=bind) as progress_session:
                ActivityRepository(progress_session).publish(activity_id, principal.user_id, steps)

        tracker = ActivityTracker(publish)
        started = time.perf_counter()
        try:
            with track_activity(tracker):
                if sensitive_input(question):
                    with activity_step("policy"):
                        answer = AssistantAnswer(
                            status=AnswerStatus.REFUSED,
                            reason=AnswerReason.SENSITIVE_DATA,
                            notice=NOTICE_PRIVACY,
                        )
                else:
                    answer = self._answers.answer(question)
        except Exception as exc:
            # AnswerService handles expected failures; this is a bug guard so that even an
            # unexpected crash is still recorded as a failed exchange.
            logger.error("answer service crashed", extra={"error_type": type(exc).__name__})
            answer = AssistantAnswer(
                status=AnswerStatus.FAILED, reason=AnswerReason.MODEL_ERROR, notice=NOTICE_FAILED
            )
        if answer.reason is AnswerReason.SENSITIVE_DATA:
            stored_question = "[Question removed because it contained sensitive information.]"
        latency_ms = round((time.perf_counter() - started) * 1000)

        # Single short write transaction: user mirror, conversation, exchange.
        self._repo.upsert_user(principal)
        if conversation is None:
            conversation = self._repo.create_conversation(
                principal.user_id, conversation_title(stored_question)
            )
        exchange = self._repo.add_exchange(
            conversation,
            user_id=principal.user_id,
            question=stored_question,
            answer_text=answer.render_text(),
            answer_payload={
                "notice": answer.notice,
                "explanation": [_claim_json(c) for c in answer.explanation],
                "key_considerations": [_claim_json(c) for c in answer.key_considerations],
                "disclaimer": answer.disclaimer,
                "activity": tracker.snapshot(),
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
        activity.exchange_id = exchange.id
        activity.steps = tracker.snapshot()
        activity.finished_at = self._clock()
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
        activities = ActivityRepository(self._session)
        if activities.requests_since(user_id, now - timedelta(minutes=1)) >= self._per_minute:
            raise RateLimitExceededError(60)
        if activities.requests_since(user_id, now - timedelta(hours=1)) >= self._per_hour:
            raise RateLimitExceededError(3600)
