from typing import Any

import pytest

from app.domain.interfaces.generation import GenerationError
from app.domain.models.answers import (
    AnswerReason,
    AnswerStatus,
    Citation,
    LabeledPassage,
    VerifiedClaim,
)
from app.infrastructure.bedrock.grounding import BedrockGroundingGate
from tests.test_answer_service import GROUNDED, FakeGenerator, _service
from tests.test_bedrock_generator import FakeConverseClient


class Gate:
    def __init__(self, issues: list[str | None]) -> None:
        self.issues = issues
        self.calls = 0

    def check(
        self,
        claims: list[VerifiedClaim],
        citations: list[Citation],
        *,
        passages: list[LabeledPassage] | None = None,
    ) -> str | None:
        assert claims and citations
        self.calls += 1
        return self.issues.pop(0)


def test_unsupported_meaning_gets_one_repair_and_cannot_publish_after_second_failure() -> None:
    generator = FakeGenerator(GROUNDED)
    service = _service(generator)
    service._grounding_gate = Gate(["numbers_or_conditions", "numbers_or_conditions"])
    answer = service.answer("What are the 1031 deadlines?")
    assert answer.status is AnswerStatus.UNSUPPORTED
    assert answer.reason is AnswerReason.CITATIONS_FAILED_VERIFICATION
    assert not answer.explanation and not answer.citations
    assert len(generator.calls) == 2


def test_repaired_answer_must_pass_grounding_before_it_is_published() -> None:
    generator = FakeGenerator(GROUNDED)
    service = _service(generator)
    service._grounding_gate = Gate(["scope_or_qualifiers", None])
    answer = service.answer("What are the 1031 deadlines?")
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert len(generator.calls) == 2
    assert "scope_or_qualifiers" in generator.calls[1][0]


def test_grounding_service_failure_does_not_publish_the_draft() -> None:
    class Broken:
        def check(
            self,
            claims: list[VerifiedClaim],
            citations: list[Citation],
            *,
            passages: list[LabeledPassage] | None = None,
        ) -> str | None:
            raise GenerationError("unavailable")

    service = _service(FakeGenerator(GROUNDED))
    service._grounding_gate = Broken()
    assert service.answer("What are the 1031 deadlines?").status is AnswerStatus.FAILED


def test_grounding_checker_receives_claims_with_their_actual_quotes() -> None:
    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            result = super().converse(**request)
            result["output"]["message"]["content"][0]["toolUse"]["name"] = "GroundingDecision"
            return result

    service = _service(FakeGenerator(GROUNDED))
    answer = service.answer("What are the 1031 deadlines?")
    client = Client(
        {"checks": [{"index": 0, "supported": False, "issue": "numbers_or_conditions"}]}
    )
    gate = BedrockGroundingGate(client, "deepseek.v3.2", "ap-southeast-2")
    assert gate.check(answer.explanation, answer.citations) == "numbers_or_conditions"
    request = client.requests[0]
    assert request["inferenceConfig"]["maxTokens"] == 350
    assert "45 days" in request["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize(
    "question", ["What are 1031 deadlines?", "Explain the deadline in simpler words"]
)
def test_original_wording_survives_context_resolution(question: str) -> None:
    from app.domain.interfaces.conversation import ConversationPlan
    from tests.test_conversation import Router

    generator = FakeGenerator(GROUNDED)
    service = _service(generator)
    service._router = Router(ConversationPlan(action="search", query="1031 identification period"))
    service.answer(question)
    assert generator.calls[0][0].startswith(question)
