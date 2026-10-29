from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models.documents import ChunkDraft
from app.infrastructure.database.models import Document, DocumentChunk, IngestionRun


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    source_id: str
    title: str
    publisher: str
    source_url: str
    document_type: str
    topics: list[str]
    content_hash: str
    ingest_signature: str
    storage_key: str | None
    fetched_at: datetime


class DocumentRepository:
    """Persistence for the source corpus. The caller owns the transaction (commit/rollback)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_ingest_signature(self, source_id: str) -> str | None:
        return self._session.scalar(
            select(Document.ingest_signature).where(Document.id == source_id)
        )

    def replace_document(
        self,
        meta: DocumentMetadata,
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
        embedding_model: str,
    ) -> None:
        """Upsert the document row and atomically swap its chunks for the new set."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")

        document = self._session.get(Document, meta.source_id)
        if document is None:
            document = Document(id=meta.source_id)
            self._session.add(document)
        document.title = meta.title
        document.publisher = meta.publisher
        document.source_url = meta.source_url
        document.document_type = meta.document_type
        document.topics = meta.topics
        document.content_hash = meta.content_hash
        document.ingest_signature = meta.ingest_signature
        document.storage_key = meta.storage_key
        document.fetched_at = meta.fetched_at

        self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == meta.source_id)
        )
        self._session.add_all(
            DocumentChunk(
                document_id=meta.source_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                section=chunk.section,
                embedding=vector,
                embedding_model=embedding_model,
            )
            for chunk, vector in zip(chunks, embeddings, strict=True)
        )
        self._session.flush()

    def record_run(
        self,
        *,
        source_id: str,
        status: str,
        started_at: datetime,
        content_hash: str | None = None,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> None:
        self._session.add(
            IngestionRun(
                source_id=source_id,
                status=status,
                started_at=started_at,
                content_hash=content_hash,
                chunk_count=chunk_count,
                error=error[:2000] if error else None,
            )
        )
        self._session.flush()
