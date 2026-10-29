"""ORM models — the single source of truth for the database schema.

Change a model, then generate a reviewed migration:
    uv run alembic revision --autogenerate -m "<change>"
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin

# Titan Text Embeddings V2 at 1024 dimensions. Changing this needs a migration + re-ingest.
EMBEDDING_DIMENSIONS = 1024


class Document(TimestampMixin, Base):
    """One curated public source from data/sources.json."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # manifest source_id
    title: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(10))
    topics: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the original bytes
    # sha256(content_hash + chunker version + embedding model): unchanged ⇒ skip re-ingest.
    ingest_signature: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(Text)  # s3:// reference to the original
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (CheckConstraint("document_type IN ('html', 'pdf')", name="document_type"),)


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)  # original text, shown to users and quote-checked
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(100))

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_chunk"),
        CheckConstraint("char_length(content) > 0", name="content_not_empty"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class IngestionRun(Base):
    """Outcome of one ingestion attempt for one source — failures are recorded, not swallowed."""

    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Not a foreign key: a source can fail before its documents row exists.
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('ingested', 'skipped', 'failed')", name="status"),
    )


class User(TimestampMixin, Base):
    """Mirror of the Cognito identity, kept so the audit view can show who asked what."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Cognito `sub`
    email: Mapped[str] = mapped_column(Text, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    exchanges: Mapped[list["Exchange"]] = relationship(
        back_populates="conversation", order_by="Exchange.created_at"
    )

    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)


class Exchange(TimestampMixin, Base):
    """One question → answer turn. Doubles as the conversation history and the audit record,
    so it is append-only (enforced by a database trigger in the migration)."""

    __tablename__ = "exchanges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT")
    )
    # Denormalised from the conversation for ownership checks, filtering and rate limiting.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    question: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)  # exactly what was shown, incl. disclaimer
    answer_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)  # structured, for the UI
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)  # citation snapshot
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(50))
    model_id: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    retrieved_passages: Mapped[int] = mapped_column(Integer, default=0)
    dropped_claims: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(64))

    conversation: Mapped[Conversation] = relationship(back_populates="exchanges")
    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint(
            "status IN ('answered', 'refused', 'unsupported', 'failed')", name="status"
        ),
        CheckConstraint("char_length(question) BETWEEN 1 AND 4000", name="question_length"),
        Index("ix_exchanges_conversation_created", "conversation_id", "created_at"),
        Index("ix_exchanges_user_created", "user_id", "created_at"),
        Index("ix_exchanges_created", "created_at"),
        Index("ix_exchanges_status", "status"),
    )


class AnswerActivity(TimestampMixin, Base):
    """Short-lived operational progress. Never contains questions or model reasoning."""

    __tablename__ = "answer_activity"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    exchange_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exchanges.id", ondelete="RESTRICT")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_answer_activity_user_created", "user_id", "created_at"),
        Index("ix_answer_activity_created", "created_at"),
    )


__all__ = [
    "AnswerActivity",
    "Base",
    "Conversation",
    "Document",
    "DocumentChunk",
    "Exchange",
    "IngestionRun",
    "User",
]
