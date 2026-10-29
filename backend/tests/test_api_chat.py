from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.api.dependencies.auth import get_token_verifier
from app.api.dependencies.services import DbSession, get_answer_service, get_chat_service
from app.application.chat.service import ChatService
from app.domain.models.answers import (
    DISCLAIMER,
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    Citation,
    VerifiedClaim,
)
from app.infrastructure.database.models import Exchange, User
from app.infrastructure.database.session import get_session_factory
from app.main import create_app
from tests.auth_helpers import auth, make_token, verifier

pytestmark = pytest.mark.integration

ADVISOR_A = make_token(sub="advisor-a", email="a@example.com")
ADVISOR_B = make_token(sub="advisor-b", email="b@example.com")
ADMIN = make_token(sub="admin-1", email="admin@example.com", groups=("admin",))


class FakeAnswerService:
    """Canned pipeline output; these tests cover auth, ownership and auditing."""

    def answer(
        self, question: str, *, history: list[dict[str, str]] | None = None
    ) -> AssistantAnswer:
        if "crash" in question:
            raise RuntimeError("unexpected bug")
        if question.startswith("Should I"):
            return AssistantAnswer(
                status=AnswerStatus.REFUSED,
                reason=AnswerReason.PERSONALIZED_ADVICE,
                notice="I can't make recommendations.",
            )
        return AssistantAnswer(
            status=AnswerStatus.ANSWERED,
            explanation=[VerifiedClaim("Identify replacement property within 45 days.", (1,))],
            citations=[
                Citation(
                    number=1,
                    chunk_id=uuid4(),
                    document_id="irs-like-kind-real-estate-tips",
                    title="Like-kind exchanges - Real estate tax tips",
                    publisher="Internal Revenue Service",
                    url="https://www.irs.gov/tips#:~:text=identified%20within%2045%20days",
                    source_url="https://www.irs.gov/tips",
                    location="Deferred exchanges",
                    quotes=("identified within 45 days",),
                )
            ],
            model_id="fake-model",
        )


@pytest.fixture
def client(migrated_db: None) -> Iterator[TestClient]:
    with get_session_factory()() as s:
        s.execute(text("SET lock_timeout = '5s'"))  # a leaked session fails fast, not hangs
        s.execute(text("TRUNCATE exchanges, conversations, users CASCADE"))
        s.commit()
    app = create_app()
    app.dependency_overrides[get_token_verifier] = verifier
    app.dependency_overrides[get_answer_service] = FakeAnswerService
    with TestClient(app) as c:
        yield c


def _ask(client: TestClient, token: str, question: str, **body: Any) -> Any:
    return client.post("/api/chat", json={"question": question, **body}, headers=auth(token))


def _exchange_count() -> int:
    with get_session_factory()() as s:
        return s.scalar(select(func.count()).select_from(Exchange)) or 0


def test_every_endpoint_requires_a_valid_token(client: TestClient) -> None:
    expired = make_token(exp=1)
    for method, path in [
        ("post", "/api/chat"),
        ("get", "/api/conversations"),
        ("get", f"/api/conversations/{uuid4()}"),
        ("get", "/api/admin/exchanges"),
        ("get", "/api/me"),
    ]:
        for headers in ({}, {"Authorization": "Bearer junk"}, auth(expired)):
            response = client.request(method, path, headers=headers, json={"question": "x" * 10})
            assert response.status_code == 401, (method, path, headers)
            assert response.headers["WWW-Authenticate"] == "Bearer"


def test_question_is_answered_persisted_and_threaded(client: TestClient) -> None:
    first = _ask(client, ADVISOR_A, "  What is the 1031   identification period? ")
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "answered"
    assert body["question"] == "What is the 1031 identification period?"
    assert body["citations"][0]["url"].startswith("https://www.irs.gov/tips")
    assert body["disclaimer"] == DISCLAIMER

    follow_up = _ask(
        client, ADVISOR_A, "Should I do one?", conversation_id=body["conversation_id"]
    ).json()
    assert follow_up["conversation_id"] == body["conversation_id"]
    assert (follow_up["status"], follow_up["reason"]) == ("refused", "personalized_advice")

    conversations = client.get("/api/conversations", headers=auth(ADVISOR_A)).json()
    assert [(c["exchange_count"], c["title"]) for c in conversations] == [
        (2, "What is the 1031 identification period?")
    ]
    detail = client.get(f"/api/conversations/{body['conversation_id']}", headers=auth(ADVISOR_A))
    assert [e["question"] for e in detail.json()["exchanges"]] == [
        "What is the 1031 identification period?",
        "Should I do one?",
    ]

    with get_session_factory()() as s:
        row = s.scalars(select(Exchange).where(Exchange.id == body["id"])).one()
        assert row.answer_text.endswith(DISCLAIMER)
        assert row.sources[0]["document_id"] == "irs-like-kind-real-estate-tips"
        assert row.created_at is not None and row.latency_ms >= 0
        assert s.get(User, "advisor-a").email == "a@example.com"  # type: ignore[union-attr]


def test_advisors_cannot_read_or_write_each_others_conversations(client: TestClient) -> None:
    conversation_id = _ask(client, ADVISOR_A, "What is a DST?").json()["conversation_id"]

    read = client.get(f"/api/conversations/{conversation_id}", headers=auth(ADVISOR_B))
    write = _ask(client, ADVISOR_B, "What is a DST?", conversation_id=conversation_id)
    listing = client.get("/api/conversations", headers=auth(ADVISOR_B))

    assert read.status_code == 404 and write.status_code == 404
    assert listing.json() == []
    assert _exchange_count() == 1


def test_client_cannot_assert_identity_or_role(client: TestClient) -> None:
    response = _ask(client, ADVISOR_A, "What is a DST?", user_id="admin-1", is_admin=True)
    assert response.status_code == 422
    assert client.get("/api/me", headers=auth(ADVISOR_A)).json()["is_admin"] is False


@pytest.mark.parametrize(
    "question", ["What kind of information you can give to me", "What is your job"]
)
def test_capabilities_answer_is_saved_and_audited_without_a_model(
    client: TestClient, question: str
) -> None:
    from app.application.rag.service import AnswerService
    from tests.test_answer_service import FakeEmbedder, FakeGenerator, FakeRetriever

    embedder = FakeEmbedder(fail=True)
    generator = FakeGenerator(RuntimeError("must not be called"))
    answers = AnswerService(embedder, FakeRetriever([]), generator, top_k=6, min_similarity=0.4)
    client.app.dependency_overrides[get_answer_service] = lambda: answers  # type: ignore[attr-defined]
    response = _ask(client, ADVISOR_A, question)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["reason"]) == ("answered", "capabilities")
    assert "1031" in body["notice"]
    assert (
        "My job is" in body["notice"]
        if question == "What is your job"
        else "For example:" in body["notice"]
    )
    assert body["citations"] == [] and body["disclaimer"] == DISCLAIMER
    assert embedder.calls == 0 and generator.calls == []
    audit = client.get("/api/admin/exchanges", headers=auth(ADMIN)).json()["items"]
    assert len(audit) == 1 and audit[0]["id"] == body["id"]
    assert audit[0]["model_id"] is None
    assert audit[0]["answer_text"].endswith(DISCLAIMER)


def test_audit_log_is_admin_only_and_shows_all_users(client: TestClient) -> None:
    _ask(client, ADVISOR_A, "What is a DST?")
    _ask(client, ADVISOR_B, "Should I buy a DST?")

    assert client.get("/api/admin/exchanges", headers=auth(ADVISOR_A)).status_code == 403

    page = client.get("/api/admin/exchanges", headers=auth(ADMIN)).json()
    assert page["total"] == 2
    assert {i["user_email"] for i in page["items"]} == {"a@example.com", "b@example.com"}
    assert page["items"][0]["question"] == "Should I buy a DST?"  # newest first
    assert all(i["answer_text"].endswith(DISCLAIMER) for i in page["items"])
    assert page["summary"] == {
        "total": 2,
        "answered": 1,
        "refused": 1,
        "unsupported": 0,
        "failed": 0,
    }

    refused = client.get("/api/admin/exchanges?status=refused", headers=auth(ADMIN)).json()
    assert [i["user_email"] for i in refused["items"]] == ["b@example.com"]
    by_user = client.get("/api/admin/exchanges?user=a%40example", headers=auth(ADMIN)).json()
    assert by_user["total"] == 1
    by_text = client.get("/api/admin/exchanges?q=50%25", headers=auth(ADMIN)).json()
    assert by_text["total"] == 0  # LIKE wildcards in search input are escaped


def test_rate_limit_rejects_without_recording_an_exchange(client: TestClient) -> None:
    def limited(session: DbSession) -> ChatService:
        return ChatService(session, FakeAnswerService(), per_minute=2, per_hour=10)  # type: ignore[arg-type]

    client.app.dependency_overrides[get_chat_service] = limited  # type: ignore[attr-defined]
    statuses = [_ask(client, ADVISOR_A, f"What is a DST? ({i})").status_code for i in range(3)]

    assert statuses == [200, 200, 429]
    assert _exchange_count() == 2


def test_unexpected_pipeline_crash_is_still_audited_as_failed(client: TestClient) -> None:
    response = _ask(client, ADVISOR_A, "please crash now")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["reason"]) == ("failed", "model_error")
    assert _exchange_count() == 1


@pytest.mark.parametrize("question", ["", "  ab ", "x" * 1001, "bad\x00byte question"])
def test_invalid_questions_are_rejected_before_processing(
    client: TestClient, question: str
) -> None:
    assert _ask(client, ADVISOR_A, question).status_code == 422
    assert _exchange_count() == 0


def test_exchanges_are_append_only_at_database_level(client: TestClient) -> None:
    _ask(client, ADVISOR_A, "What is a DST?")
    with get_session_factory()() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("UPDATE exchanges SET answer_text = 'tampered'"))
        s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("DELETE FROM exchanges"))


def test_activity_is_owned_and_replays_do_not_duplicate_an_exchange(client: TestClient) -> None:
    activity_id = str(uuid4())
    first = _ask(client, ADVISOR_A, "What is a DST?", activity_id=activity_id)
    assert first.status_code == 200
    again = _ask(client, ADVISOR_A, "What is a DST?", activity_id=activity_id)
    assert again.json()["id"] == first.json()["id"]
    assert _exchange_count() == 1
    progress = client.get(f"/api/chat/activity/{activity_id}", headers=auth(ADVISOR_A))
    assert progress.status_code == 200
    assert progress.json()["finished"] is True
    assert progress.json()["exchange_id"] == first.json()["id"]
    assert client.get(f"/api/chat/activity/{activity_id}").status_code == 401
    assert (
        client.get(f"/api/chat/activity/{activity_id}", headers=auth(ADVISOR_B)).status_code == 404
    )
    conflict = _ask(client, ADVISOR_A, "A different question?", activity_id=activity_id)
    assert conflict.status_code == 409 and _exchange_count() == 1


def test_private_identifiers_are_removed_before_history_and_audit(client: TestClient) -> None:
    private = "123-45-6789"
    response = _ask(client, ADVISOR_A, f"My SSN is {private}. What is a DST?")
    assert response.status_code == 200
    assert response.json()["reason"] == "sensitive_data"
    assert private not in str(response.json())
    history = client.get("/api/conversations", headers=auth(ADVISOR_A)).json()
    assert private not in str(history)
    audit = client.get("/api/admin/exchanges", headers=auth(ADMIN)).json()
    assert private not in str(audit)


def test_activity_updates_while_request_runs_and_limits_concurrent_requests(
    client: TestClient,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Condition, Event

    from app.application.rag.activity import activity_step

    started, release = Condition(), Event()
    calls = [0]

    class BlockingAnswers:
        def answer(
            self, question: str, *, history: list[dict[str, str]] | None = None
        ) -> AssistantAnswer:
            with activity_step("retrieval"):
                with started:
                    calls[0] += 1
                    started.notify_all()
                release.wait(timeout=10)
            return AssistantAnswer(status=AnswerStatus.UNSUPPORTED)

    client.app.dependency_overrides[get_answer_service] = BlockingAnswers  # type: ignore[attr-defined]
    ids = [str(uuid4()), str(uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(_ask, client, ADVISOR_A, "What is a DST?", activity_id=id_) for id_ in ids
        ]
        try:
            with started:
                assert started.wait_for(lambda: calls[0] == 2, timeout=5)
            running = client.get(f"/api/chat/activity/{ids[0]}", headers=auth(ADVISOR_A)).json()
            assert running["finished"] is False
            assert running["steps"][0]["kind"] == "retrieval"
            assert running["steps"][0]["status"] == "running"
            third = _ask(client, ADVISOR_A, "What is a DST?")
            assert third.status_code == 429 and third.headers["Retry-After"] == "5"
            assert _ask(client, ADVISOR_A, "What is a DST?", activity_id=ids[0]).status_code == 409
        finally:
            release.set()
        assert all(p.result().status_code == 200 for p in pending)
    finished = client.get(f"/api/chat/activity/{ids[0]}", headers=auth(ADVISOR_A)).json()
    assert finished["finished"] is True
    assert finished["steps"][0]["status"] == "completed"


def test_global_answer_capacity_is_reserved_before_generation(client: TestClient) -> None:
    from app.infrastructure.database.models import AnswerActivity

    with get_session_factory()() as session:
        session.add_all(
            [
                AnswerActivity(id=uuid4(), user_id=f"busy-{i}", fingerprint="0" * 64, steps=[])
                for i in range(4)
            ]
        )
        session.commit()
    response = _ask(client, ADVISOR_A, "What is a DST?")
    assert response.status_code == 429 and response.headers["Retry-After"] == "5"
    assert _exchange_count() == 0


def test_background_request_is_claimed_once_and_result_is_private(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.dependencies.services import get_answer_dispatcher
    from app.application.chat.worker import process_answer_event

    class Dispatcher:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        def dispatch(self, payload: dict[str, Any]) -> None:
            self.events.append(payload)

    dispatcher = Dispatcher()
    client.app.dependency_overrides[get_answer_dispatcher] = lambda: dispatcher  # type: ignore[attr-defined]
    nonce = str(uuid4())
    response = _ask(client, ADVISOR_A, "What is a DST?", activity_id=nonce, background=True)
    assert response.status_code == 202 and response.json()["finished"] is False
    assert len(dispatcher.events) == 1
    assert (
        client.get(f"/api/chat/activity/{nonce}/result", headers=auth(ADVISOR_A)).status_code == 409
    )
    assert (
        client.get(f"/api/chat/activity/{nonce}/result", headers=auth(ADVISOR_B)).status_code == 404
    )
    monkeypatch.setattr(
        "app.application.chat.worker.build_answer_service",
        lambda *args, **kwargs: FakeAnswerService(),
    )
    tampered = {**dispatcher.events[0], "question": "Changed payload"}
    assert process_answer_event(tampered)["status"] == "ignored"
    assert (
        process_answer_event({**dispatcher.events[0], "requestContext": {}})["status"] == "ignored"
    )
    assert process_answer_event(dispatcher.events[0])["status"] == "completed"
    assert process_answer_event(dispatcher.events[0])["status"] == "ignored"
    result = client.get(f"/api/chat/activity/{nonce}/result", headers=auth(ADVISOR_A))
    assert result.status_code == 200
    assert _exchange_count() == 1
    again = _ask(client, ADVISOR_A, "What is a DST?", activity_id=nonce, background=True)
    assert again.status_code == 200 and again.json()["id"] == result.json()["id"]
    assert len(dispatcher.events) == 1


def test_background_privacy_refusal_is_immediate_and_never_dispatched(client: TestClient) -> None:
    from app.api.dependencies.services import get_answer_dispatcher

    class Dispatcher:
        def dispatch(self, payload: dict[str, Any]) -> None:
            raise AssertionError("private input must not enter the worker event")

    client.app.dependency_overrides[get_answer_dispatcher] = Dispatcher  # type: ignore[attr-defined]
    response = _ask(client, ADVISOR_A, "My SSN is 123-45-6789. Explain a DST", background=True)
    assert response.status_code == 200
    assert response.json()["reason"] == "sensitive_data"
    assert "123-45-6789" not in str(response.json())


def test_expired_background_request_cannot_bypass_quotas_by_being_replayed(
    client: TestClient,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.infrastructure.database.models import AnswerActivity

    nonce = uuid4()
    question = "What is a DST?"
    import hashlib

    with get_session_factory()() as session:
        session.add(
            AnswerActivity(
                id=nonce,
                user_id="advisor-a",
                fingerprint=hashlib.sha256(f"None:{question}".encode()).hexdigest(),
                steps=[],
                created_at=datetime.now(UTC) - timedelta(seconds=140),
            )
        )
        session.commit()
    response = _ask(client, ADVISOR_A, question, activity_id=str(nonce), background=True)
    assert response.status_code == 410
    assert _exchange_count() == 0


def test_result_ownership_is_checked_even_if_a_telemetry_link_is_wrong(client: TestClient) -> None:
    from app.infrastructure.database.models import AnswerActivity

    nonce = str(uuid4())
    _ask(client, ADVISOR_A, "What is a DST?", activity_id=nonce)
    other = _ask(client, ADVISOR_B, "What is a DST?").json()
    with get_session_factory()() as session:
        record = session.get(AnswerActivity, UUID(nonce))
        assert record is not None
        record.exchange_id = UUID(other["id"])
        session.commit()
    assert (
        client.get(f"/api/chat/activity/{nonce}/result", headers=auth(ADVISOR_A)).status_code == 404
    )
    assert _ask(client, ADVISOR_A, "What is a DST?", activity_id=nonce).status_code == 404


def test_follow_up_receives_only_its_owned_recent_context(client: TestClient) -> None:
    calls: list[list[dict[str, str]]] = []

    class RecordingAnswers(FakeAnswerService):
        def answer(
            self, question: str, *, history: list[dict[str, str]] | None = None
        ) -> AssistantAnswer:
            calls.append(history or [])
            return super().answer(question, history=history)

    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_answer_service] = RecordingAnswers
    first = _ask(client, ADVISOR_A, "Explain the 1031 identification period").json()
    _ask(client, ADVISOR_B, "Explain unrelated private conversation")
    _ask(client, ADVISOR_A, "Explain a separate DST conversation")
    _ask(client, ADVISOR_A, "And the deadline?", conversation_id=first["conversation_id"])
    assert len(calls[-1]) == 1
    assert calls[-1][0]["question"] == first["question"]
    before = len(calls)
    foreign = _ask(client, ADVISOR_B, "And the deadline?", conversation_id=first["conversation_id"])
    assert foreign.status_code == 404 and len(calls) == before


def test_reusable_evidence_requires_owner_current_chunk_and_latest_answer(
    client: TestClient,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.infrastructure.database.models import Document, DocumentChunk
    from app.infrastructure.repositories.chat import ChatRepository

    source_id = f"reuse-{uuid4().hex[:12]}"
    with get_session_factory()() as session:
        document = Document(
            id=source_id,
            title="Exchange guide",
            publisher="IRS",
            source_url="https://www.irs.gov/example",
            document_type="html",
            topics=["1031"],
            content_hash="a" * 64,
            ingest_signature="b" * 64,
            storage_key=None,
            fetched_at=datetime.now(UTC) - timedelta(days=1),
        )
        chunk = DocumentChunk(
            document_id=source_id,
            chunk_index=0,
            # A PDF line break inside the quoted span must not disable reuse.
            content="Replacement property must be identified\nwithin 45 days of sale.",
            page_number=None,
            section="Deadline",
            embedding=[1.0] + [0.0] * 1023,
            embedding_model="titan-test",
        )
        session.add_all([document, chunk])
        session.commit()
        chunk_id = chunk.id
    result = _ask(client, ADVISOR_A, "Explain the 1031 identification period").json()
    conversation_id = UUID(result["conversation_id"])
    with get_session_factory()() as session:
        from app.infrastructure.database.models import Conversation

        conversation = session.get(Conversation, conversation_id)
        assert conversation is not None
        repo = ChatRepository(session)
        repo.add_exchange(
            conversation,
            user_id="advisor-a",
            question="Explain the cited deadline",
            answer_text="A sourced explanation.",
            answer_payload={"disclaimer": "Educational only."},
            sources=[
                {
                    "chunk_id": str(chunk_id),
                    "document_id": source_id,
                    "quotes": ["identified within 45 days"],
                }
            ],
            status="answered",
            reason=None,
            model_id="test",
            input_tokens=None,
            output_tokens=None,
            latency_ms=1,
            retrieved_passages=1,
            dropped_claims=0,
            request_id=None,
        )
        session.commit()
        assert len(repo.reusable_evidence(conversation_id, "advisor-a", "titan-test")) == 1
        assert len(repo.reusable_citations(conversation_id, "advisor-a", "titan-test")) == 1
        assert repo.reusable_evidence(conversation_id, "advisor-b", "titan-test") == []
        assert repo.reusable_citations(conversation_id, "advisor-b", "titan-test") == []
        assert repo.reusable_evidence(conversation_id, "advisor-a", "other-model") == []
        refreshed_document = session.get(Document, source_id)
        assert refreshed_document is not None
        refreshed_document.updated_at = datetime.now(UTC) + timedelta(minutes=1)
        session.commit()
        assert repo.reusable_evidence(conversation_id, "advisor-a", "titan-test") == []
    _ask(client, ADVISOR_A, "Should I buy a DST?", conversation_id=str(conversation_id))
    with get_session_factory()() as session:
        assert (
            ChatRepository(session).reusable_evidence(conversation_id, "advisor-a", "titan-test")
            == []
        )


def test_refusal_keeps_topic_context_but_private_input_is_excluded(client: TestClient) -> None:
    seen: list[list[dict[str, str]]] = []

    class RecordingAnswers(FakeAnswerService):
        def answer(
            self, question: str, *, history: list[dict[str, str]] | None = None
        ) -> AssistantAnswer:
            seen.append(history or [])
            return super().answer(question, history=history)

    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_answer_service] = RecordingAnswers
    first = _ask(client, ADVISOR_A, "Should I buy a DST?").json()
    _ask(
        client,
        ADVISOR_A,
        "My SSN is 123-45-6789. Explain DSTs",
        conversation_id=first["conversation_id"],
    )
    _ask(
        client,
        ADVISOR_A,
        "Explain the general risks instead",
        conversation_id=first["conversation_id"],
    )
    assert len(seen[-1]) == 1 and seen[-1][0]["question"] == "Should I buy a DST?"
    assert "123-45-6789" not in str(seen)


def test_restatements_reuse_evidence_from_recent_answers_latest_first(client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    from app.infrastructure.database.models import Conversation, Document, DocumentChunk
    from app.infrastructure.repositories.chat import ChatRepository

    source_id = f"reuse-depth-{uuid4().hex[:8]}"
    texts = [
        "Replacement property must be identified within 45 days of sale.",
        "Replacement property must be received within 180 days of sale.",
    ]
    with get_session_factory()() as session:
        session.add(
            Document(
                id=source_id,
                title="Exchange guide",
                publisher="IRS",
                source_url="https://www.irs.gov/example",
                document_type="html",
                topics=["1031"],
                content_hash="c" * 64,
                ingest_signature="d" * 64,
                storage_key=None,
                fetched_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        chunks = [
            DocumentChunk(
                document_id=source_id,
                chunk_index=i,
                content=text,
                page_number=None,
                section="Deadlines",
                embedding=[1.0] + [0.0] * 1023,
                embedding_model="titan-depth",
            )
            for i, text in enumerate(texts)
        ]
        session.add_all(chunks)
        session.commit()
        chunk_ids = [c.id for c in chunks]
    result = _ask(client, ADVISOR_A, "Explain the 1031 identification period").json()
    conversation_id = UUID(result["conversation_id"])

    def record(status: str, chunk_index: int | None) -> None:
        with get_session_factory()() as session:
            conversation = session.get(Conversation, conversation_id)
            assert conversation is not None
            sources = (
                []
                if chunk_index is None
                else [
                    {
                        "chunk_id": str(chunk_ids[chunk_index]),
                        "document_id": source_id,
                        "quotes": [texts[chunk_index].split(" must be ")[1]],
                    }
                ]
            )
            ChatRepository(session).add_exchange(
                conversation,
                user_id="advisor-a",
                question="q",
                answer_text="An answer.",
                answer_payload={"disclaimer": "Educational only."},
                sources=sources,
                status=status,
                reason=None,
                model_id="test",
                input_tokens=None,
                output_tokens=None,
                latency_ms=1,
                retrieved_passages=len(sources),
                dropped_claims=0,
                request_id=None,
            )
            session.commit()

    def evidence() -> list[str]:
        with get_session_factory()() as session:
            repo = ChatRepository(session)
            return [
                p.content
                for p in repo.reusable_evidence(conversation_id, "advisor-a", "titan-depth")
            ]

    record("answered", 0)
    record("answered", 1)
    assert evidence() == [texts[1], texts[0]]  # latest answer's passage first
    record("refused", None)
    assert evidence() == []  # never reaches back across a refusal
