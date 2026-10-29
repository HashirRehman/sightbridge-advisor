import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion.chunking import CHUNKER_VERSION, chunk_sections, embedding_text
from app.application.ingestion.extraction import extract_html, extract_pdf
from app.application.ingestion.fetcher import FetchedSource
from app.application.ingestion.manifest import SourceManifest, SourceSpec
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.storage import ObjectStore
from app.infrastructure.repositories.documents import DocumentMetadata, DocumentRepository

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32


class Fetcher(Protocol):
    def fetch(self, spec: SourceSpec) -> FetchedSource: ...


@dataclass(frozen=True, slots=True)
class SourceResult:
    source_id: str
    status: Literal["ingested", "skipped", "failed"]
    sha256: str | None = None
    fetched_at: datetime | None = None
    chunk_count: int | None = None
    error: str | None = None


def ingest_signature(content_hash: str, embedding_model: str) -> str:
    return hashlib.sha256(
        f"{content_hash}|{CHUNKER_VERSION}|{embedding_model}".encode()
    ).hexdigest()


class IngestionService:
    """Idempotent, per-source ingestion. One source failing never blocks the others,
    and every attempt is recorded in `ingestion_runs`."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedder: Embedder,
        fetcher: Fetcher,
        object_store: ObjectStore,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._fetcher = fetcher
        self._object_store = object_store

    def ingest_all(self, manifest: SourceManifest, *, force: bool = False) -> list[SourceResult]:
        return [self.ingest_one(spec, force=force) for spec in manifest.sources]

    def ingest_one(self, spec: SourceSpec, *, force: bool = False) -> SourceResult:
        started_at = datetime.now(UTC)
        fetched: FetchedSource | None = None
        try:
            fetched = self._fetcher.fetch(spec)
            signature = ingest_signature(fetched.sha256, self._embedder.model_id)

            with self._session_factory() as session:
                repo = DocumentRepository(session)
                if not force and repo.get_ingest_signature(spec.source_id) == signature:
                    repo.record_run(
                        source_id=spec.source_id,
                        status="skipped",
                        started_at=started_at,
                        content_hash=fetched.sha256,
                    )
                    session.commit()
                    return SourceResult(
                        spec.source_id, "skipped", fetched.sha256, fetched.fetched_at
                    )

            # Expensive work happens outside the DB transaction.
            sections = (
                extract_pdf(fetched.data)
                if spec.document_type == "pdf"
                else extract_html(fetched.data, spec.content_selector or "main")
            )
            chunks = chunk_sections(sections)
            if not chunks:
                raise ValueError("no chunks produced")
            texts = [embedding_text(spec.title, c) for c in chunks]
            embeddings: list[list[float]] = []
            for i in range(0, len(texts), EMBED_BATCH_SIZE):
                embeddings.extend(self._embedder.embed_documents(texts[i : i + EMBED_BATCH_SIZE]))

            ext = "pdf" if spec.document_type == "pdf" else "html"
            storage_key = self._object_store.put(
                f"sources/{spec.source_id}/{fetched.sha256}.{ext}",
                fetched.data,
                fetched.content_type,
            )

            with self._session_factory() as session:
                repo = DocumentRepository(session)
                repo.replace_document(
                    DocumentMetadata(
                        source_id=spec.source_id,
                        title=spec.title,
                        publisher=spec.publisher,
                        source_url=str(spec.url),
                        document_type=spec.document_type,
                        topics=spec.topics,
                        content_hash=fetched.sha256,
                        ingest_signature=signature,
                        storage_key=storage_key,
                        fetched_at=fetched.fetched_at,
                    ),
                    chunks,
                    embeddings,
                    self._embedder.model_id,
                )
                repo.record_run(
                    source_id=spec.source_id,
                    status="ingested",
                    started_at=started_at,
                    content_hash=fetched.sha256,
                    chunk_count=len(chunks),
                )
                session.commit()
            logger.info(
                "ingested source", extra={"source_id": spec.source_id, "chunks": len(chunks)}
            )
            return SourceResult(
                spec.source_id, "ingested", fetched.sha256, fetched.fetched_at, len(chunks)
            )

        except Exception as exc:
            logger.exception("ingestion failed", extra={"source_id": spec.source_id})
            error = f"{type(exc).__name__}: {exc}"
            with self._session_factory() as session:
                DocumentRepository(session).record_run(
                    source_id=spec.source_id,
                    status="failed",
                    started_at=started_at,
                    content_hash=fetched.sha256 if fetched else None,
                    error=error,
                )
                session.commit()
            return SourceResult(spec.source_id, "failed", error=error)
