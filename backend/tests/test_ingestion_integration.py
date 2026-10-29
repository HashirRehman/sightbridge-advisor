import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion.fetcher import FetchedSource, FetchError
from app.application.ingestion.manifest import SourceManifest, SourceSpec
from app.application.ingestion.service import IngestionService
from app.infrastructure.database.models import DocumentChunk, IngestionRun
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.retrieval.local_embedder import LocalHashingEmbedder
from app.infrastructure.retrieval.pgvector_retriever import PgVectorPassageRetriever
from app.infrastructure.storage import NullObjectStore

pytestmark = pytest.mark.integration

PAGES = {
    "irs-tips": b"""<html><main><h2>Deferred exchanges</h2><p>The replacement property must be
        identified within 45 days and received within 180 days after the transfer of the
        relinquished property. A qualified intermediary holds the proceeds.</p></main></html>""",
    "reit-basics": b"""<html><main><h2>What are REITs?</h2><p>A real estate investment trust is
        a company that owns and operates income-producing real estate such as apartments,
        warehouses and office buildings.</p></main></html>""",
}


def _spec(source_id: str) -> SourceSpec:
    return SourceSpec(
        source_id=source_id,
        title=source_id.replace("-", " ").title(),
        publisher="Test Publisher",
        url=f"https://example.gov/{source_id}",
        document_type="html",
        topics=["1031"],
        content_selector="main",
    )


def _manifest(*ids: str) -> SourceManifest:
    specs = [_spec(i) for i in ids]
    # The real manifest needs ≥5 sources; build past validation for focused tests.
    return SourceManifest.model_construct(description="test", sources=specs)


@dataclass
class FakeFetcher:
    pages: dict[str, bytes]
    failing: set[str] = field(default_factory=set)

    def fetch(self, spec: SourceSpec) -> FetchedSource:
        if spec.source_id in self.failing:
            raise FetchError("download failed: 503")
        data = self.pages[spec.source_id]
        return FetchedSource(data, hashlib.sha256(data).hexdigest(), datetime.now(UTC), "text/html")


@pytest.fixture
def session_factory(migrated_db: None) -> Iterator[sessionmaker[Session]]:
    factory = get_session_factory()
    with factory() as s:
        s.execute(text("SET lock_timeout = '5s'"))
        s.execute(text("TRUNCATE documents, document_chunks, ingestion_runs CASCADE"))
        s.commit()
    yield factory


def _service(factory: sessionmaker[Session], fetcher: FakeFetcher) -> IngestionService:
    return IngestionService(factory, LocalHashingEmbedder(), fetcher, NullObjectStore())


def test_reingestion_is_idempotent_and_changes_replace_chunks(
    session_factory: sessionmaker[Session],
) -> None:
    fetcher = FakeFetcher(dict(PAGES))
    manifest = _manifest("irs-tips", "reit-basics")

    first = _service(session_factory, fetcher).ingest_all(manifest)
    second = _service(session_factory, fetcher).ingest_all(manifest)
    fetcher.pages["irs-tips"] = b"<html><main><p>Updated guidance text.</p></main></html>"
    third = _service(session_factory, fetcher).ingest_all(manifest)

    assert [r.status for r in first] == ["ingested", "ingested"]
    assert [r.status for r in second] == ["skipped", "skipped"]
    assert [r.status for r in third] == ["ingested", "skipped"]
    with session_factory() as s:
        contents = s.scalars(
            select(DocumentChunk.content).where(DocumentChunk.document_id == "irs-tips")
        ).all()
        assert contents == ["Updated guidance text."]
        assert s.scalar(select(func.count()).select_from(IngestionRun)) == 6


def test_failed_source_is_recorded_without_blocking_others(
    session_factory: sessionmaker[Session],
) -> None:
    fetcher = FakeFetcher(dict(PAGES), failing={"irs-tips"})

    results = _service(session_factory, fetcher).ingest_all(_manifest("irs-tips", "reit-basics"))

    assert [r.status for r in results] == ["failed", "ingested"]
    with session_factory() as s:
        run = s.scalars(select(IngestionRun).where(IngestionRun.source_id == "irs-tips")).one()
        assert run.status == "failed" and "503" in (run.error or "")


def test_retrieval_returns_relevant_passage_with_source_metadata(
    session_factory: sessionmaker[Session],
) -> None:
    embedder = LocalHashingEmbedder()
    _service(session_factory, FakeFetcher(dict(PAGES))).ingest_all(
        _manifest("irs-tips", "reit-basics")
    )

    with session_factory() as s:
        retriever = PgVectorPassageRetriever(s, embedder.model_id)
        query = embedder.embed_query("How many days to identify replacement property?")
        hits = retriever.search(query, top_k=2, min_similarity=0.0)
        strict = retriever.search(query, top_k=2, min_similarity=0.99)
        other_model = PgVectorPassageRetriever(s, "some-other-model").search(
            query, top_k=2, min_similarity=0.0
        )

    top = hits[0]
    assert top.document_id == "irs-tips"
    assert (top.title, top.publisher, top.source_url, top.section) == (
        "Irs Tips",
        "Test Publisher",
        "https://example.gov/irs-tips",
        "Deferred exchanges",
    )
    assert "45 days" in top.content
    assert hits[0].similarity >= hits[1].similarity
    assert strict == []
    assert other_model == []  # vectors from different embedding models are never mixed
