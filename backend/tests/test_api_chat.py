from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
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

    def answer(self, question: str) -> AssistantAnswer:
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
