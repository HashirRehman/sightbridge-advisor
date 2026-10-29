"""ORM models — the single source of truth for the database schema.

Change a model, then generate a reviewed migration:
    uv run alembic revision --autogenerate -m "<change>"
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
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


__all__ = ["Base", "Document", "DocumentChunk", "IngestionRun"]
