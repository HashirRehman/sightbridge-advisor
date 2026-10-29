import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.infrastructure.database.models import Conversation, Exchange

MAX_QUESTION_CHARS = 1000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Status = Literal["answered", "refused", "unsupported", "failed"]


class ChatRequest(BaseModel):
    # Identity and role come only from the verified token; unknown fields are rejected.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=MAX_QUESTION_CHARS)
    conversation_id: uuid.UUID | None = None

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("question contains control characters")
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("question is too short")
        return cleaned


class ClaimOut(BaseModel):
    text: str
    citations: list[int]


class CitationOut(BaseModel):
    number: int
    document_id: str
    title: str
    publisher: str
    url: str
    source_url: str
    location: str | None
    quotes: list[str]


class ExchangeOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    question: str
    status: Status
    reason: str | None
    notice: str | None
    explanation: list[ClaimOut]
    key_considerations: list[ClaimOut]
    citations: list[CitationOut]
    disclaimer: str
    created_at: datetime

    @classmethod
    def from_model(cls, e: Exchange) -> "ExchangeOut":
        payload: dict[str, Any] = e.answer_payload
        return cls(
            id=e.id,
            conversation_id=e.conversation_id,
            question=e.question,
            status=e.status,
            reason=e.reason,
            notice=payload.get("notice"),
            explanation=payload.get("explanation", []),
            key_considerations=payload.get("key_considerations", []),
            citations=[CitationOut.model_validate(s) for s in e.sources],
            disclaimer=payload["disclaimer"],
            created_at=e.created_at,
        )


class ConversationSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    exchange_count: int
    updated_at: datetime


class ConversationDetailOut(BaseModel):
    id: uuid.UUID
    title: str
    exchanges: list[ExchangeOut]

    @classmethod
    def from_model(cls, c: Conversation, exchanges: list[Exchange]) -> "ConversationDetailOut":
        return cls(id=c.id, title=c.title, exchanges=[ExchangeOut.from_model(e) for e in exchanges])


class AdminExchangeOut(ExchangeOut):
    user_id: str
    user_email: str
    answer_text: str
    model_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    retrieved_passages: int
    dropped_claims: int
    request_id: str | None

    @classmethod
    def from_model(cls, e: Exchange) -> "AdminExchangeOut":
        base = ExchangeOut.from_model(e).model_dump()
        return cls(
            **base,
            user_id=e.user_id,
            user_email=e.user.email,
            answer_text=e.answer_text,
            model_id=e.model_id,
            input_tokens=e.input_tokens,
            output_tokens=e.output_tokens,
            latency_ms=e.latency_ms,
            retrieved_passages=e.retrieved_passages,
            dropped_claims=e.dropped_claims,
            request_id=e.request_id,
        )


class AuditSummaryOut(BaseModel):
    total: int
    answered: int
    refused: int
    unsupported: int
    failed: int


class AdminExchangePageOut(BaseModel):
    items: list[AdminExchangeOut]
    total: int  # matching the filter
    limit: int
    offset: int
    summary: AuditSummaryOut  # across all exchanges


class MeOut(BaseModel):
    user_id: str
    email: str
    is_admin: bool
