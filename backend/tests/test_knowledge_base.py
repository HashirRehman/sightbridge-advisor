from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies.auth import get_token_verifier
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.session import get_session_factory
from app.main import create_app
from tests.auth_helpers import auth, make_token, verifier

pytestmark = pytest.mark.integration
ADMIN = auth(make_token(sub="corpus-admin", email="admin@example.com", groups=("admin",)))
ADVISOR = auth(make_token(sub="corpus-advisor", email="advisor@example.com"))


@pytest.fixture
def corpus_client(migrated_db: None) -> Iterator[TestClient]:
    def clear() -> None:
        with get_session_factory()() as session:
            session.execute(text("TRUNCATE documents, document_chunks, ingestion_runs CASCADE"))
            session.commit()

    clear()
    app = create_app()
    app.dependency_overrides[get_token_verifier] = verifier
    with TestClient(app) as client:
        yield client
    clear()


def seed_corpus() -> None:
    with get_session_factory()() as session:
        for source in ("populated", "empty"):
            session.add(
                Document(
                    id=source,
                    title=f"Source {source}",
                    publisher="IRS",
                    source_url=f"https://www.irs.gov/{source}",
                    document_type="html",
                    topics=["1031"],
                    content_hash="a" * 64,
                    ingest_signature="b" * 64,
                    storage_key=None,
                    fetched_at=datetime.now(UTC),
                )
            )
        session.flush()
        for index, model in enumerate(["titan-test", "titan-test", "cohere-test"]):
            session.add(
                DocumentChunk(
                    document_id="populated",
                    chunk_index=index,
                    content=f"Stored passage {index}.",
                    page_number=None,
                    section="Overview",
                    embedding=[1.0] + [0.0] * 1023,
                    embedding_model=model,
                )
            )
        session.commit()


@pytest.mark.parametrize("path", ["/api/admin/knowledge-base", "/api/admin/documents/x/chunks"])
def test_corpus_endpoints_require_admin(corpus_client: TestClient, path: str) -> None:
    assert corpus_client.get(path).status_code == 401
    assert corpus_client.get(path, headers=ADVISOR).status_code == 403


def test_inventory_counts_actual_documents_and_model_vectors(corpus_client: TestClient) -> None:
    seed_corpus()
    response = corpus_client.get("/api/admin/knowledge-base", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert (body["document_count"], body["chunk_count"]) == (2, 3)
    assert body["embeddings"] == [
        {"model_id": "cohere-test", "dimensions": 1024, "chunk_count": 1},
        {"model_id": "titan-test", "dimensions": 1024, "chunk_count": 2},
    ]
    assert [(d["id"], d["chunk_count"]) for d in body["documents"]] == [
        ("empty", 0),
        ("populated", 3),
    ]
    assert body["query_embedding_model"].startswith("local-hashing-v1-")
    assert "storage_key" not in body["documents"][0]


def test_chunk_pagination_exposes_text_and_bounded_vector_preview(
    corpus_client: TestClient,
) -> None:
    seed_corpus()
    response = corpus_client.get(
        "/api/admin/documents/populated/chunks?offset=1&limit=1",
        headers=ADMIN,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["offset"], body["limit"]) == (3, 1, 1)
    chunk = body["items"][0]
    assert chunk["chunk_index"] == 1
    assert chunk["content"] == "Stored passage 1."
    assert chunk["dimensions"] == 1024
    assert chunk["embedding_preview"] == [1.0] + [0.0] * 7
    assert "embedding" not in chunk
    assert (
        corpus_client.get("/api/admin/documents/missing/chunks", headers=ADMIN).status_code == 404
    )
    assert (
        corpus_client.get(
            "/api/admin/documents/populated/chunks?limit=1000",
            headers=ADMIN,
        ).status_code
        == 422
    )
    beyond = corpus_client.get(
        "/api/admin/documents/populated/chunks?offset=99",
        headers=ADMIN,
    ).json()
    assert beyond["total"] == 3 and beyond["items"] == []


def test_empty_corpus_is_reported_without_placeholder_counts(corpus_client: TestClient) -> None:
    body = corpus_client.get("/api/admin/knowledge-base", headers=ADMIN).json()
    assert body["document_count"] == body["chunk_count"] == 0
    assert body["documents"] == body["embeddings"] == []
