from threading import Event

import pytest

from app.application.rag.budget import answer_budget
from app.application.rag.fallback import FallbackAnswerGenerator
from app.core.config import Settings
from app.domain.interfaces.generation import GenerationError, GenerationMode
from app.infrastructure.bedrock.client import build_bedrock_runtime_client
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.bedrock.generator import BedrockAnswerGenerator
from app.infrastructure.bedrock.invocation import invoke_bounded
from tests.test_bedrock_generator import MODEL, FakeConverseClient


def test_slow_call_returns_without_waiting_for_background_completion() -> None:
    release, finished = Event(), Event()

    def slow() -> None:
        release.wait(timeout=5)
        finished.set()

    try:
        with answer_budget(0.02), pytest.raises(TimeoutError):
            invoke_bounded(slow, 8)
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(timeout=5)


def test_exhausted_shared_budget_does_not_start_another_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr("app.application.rag.budget.time.monotonic", lambda: now[0])
    invoked: list[str] = []
    with answer_budget(20):
        assert invoke_bounded(lambda: "embedding", 4) == "embedding"
        now[0] = 21
        with pytest.raises(TimeoutError, match="budget exhausted"):
            invoke_bounded(lambda: invoked.append("fallback"), 8)
    assert invoked == []
    # The deadline must not leak into subsequent requests or operator-side work.
    assert invoke_bounded(lambda: "next request", 1) == "next request"


def test_generation_timeout_uses_fallback_and_discards_late_primary_result() -> None:
    release = Event()

    class SlowClient(FakeConverseClient):
        def converse(self, **request: object) -> dict[str, object]:
            release.wait(timeout=5)
            return super().converse(**request)

    primary = SlowClient({"answerable": False, "explanation": [], "key_considerations": []})
    fallback = FakeConverseClient(
        {"answerable": False, "explanation": [], "key_considerations": []}
    )
    generators = [
        BedrockAnswerGenerator(primary, MODEL, 900, "us-east-1", timeout_seconds=0.02),
        BedrockAnswerGenerator(fallback, MODEL, 900, "us-east-1"),
    ]
    try:
        result = FallbackAnswerGenerator(generators).generate("1031?", [], GenerationMode.STANDARD)
        assert result.model_id == MODEL
        assert len(fallback.requests) == 1
    finally:
        release.set()


def test_expired_budget_becomes_safe_generation_error_without_model_call() -> None:
    client = FakeConverseClient()
    generator = BedrockAnswerGenerator(client, MODEL, 900, "us-east-1")
    with answer_budget(0), pytest.raises(GenerationError, match="timed out"):
        generator.generate("1031?", [], GenerationMode.STANDARD)
    assert client.requests == []


def test_runtime_client_has_no_hidden_retries() -> None:
    client = build_bedrock_runtime_client(Settings(_env_file=None))
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.read_timeout == 30


def test_socket_timeout_never_cuts_off_the_generation_allowance() -> None:
    settings = Settings(_env_file=None)
    assert settings.bedrock_timeout_seconds >= settings.worker_generation_timeout_seconds


def test_answer_budget_cannot_consume_the_entire_lambda_timeout() -> None:
    with pytest.raises(ValueError, match="answer_timeout_seconds"):
        Settings(_env_file=None, answer_timeout_seconds=29)


def test_slow_query_embedding_fails_safely_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.application.rag.service import AnswerService
    from app.domain.models.answers import DISCLAIMER, AnswerStatus
    from tests.factories import draft
    from tests.test_answer_service import FakeGenerator, FakeRetriever

    release, finished = Event(), Event()

    def slow_embedding(self: object, text: str) -> list[float]:
        release.wait(timeout=5)
        finished.set()
        return [1.0]

    monkeypatch.setattr(
        "app.infrastructure.bedrock.embeddings.BedrockEmbeddings.embed_query", slow_embedding
    )
    embedder = BedrockTitanEmbedder(object(), "amazon.titan-embed-text-v2:0", 1024, 0.02)
    generator = FakeGenerator(draft(answerable=False))
    service = AnswerService(embedder, FakeRetriever([]), generator, top_k=6, min_similarity=0.3)
    try:
        answer = service.answer("What is a 1031 exchange?")
        assert answer.status is AnswerStatus.FAILED
        assert answer.render_text().endswith(DISCLAIMER)
        assert generator.calls == []
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(timeout=5)
