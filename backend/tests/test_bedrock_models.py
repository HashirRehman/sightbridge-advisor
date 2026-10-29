"""Bedrock adapters for planning, review and embeddings, against a fake Converse client."""

import io
import json
from typing import Any
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from app.application.rag.activity import ActivityTracker, track_activity
from app.domain.interfaces.generation import GenerationError
from app.domain.interfaces.safety import SafetyCheckError, SafetyVerdict
from app.domain.models.answers import Citation, VerifiedClaim
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.bedrock.metering import MeteredBedrockClient
from app.infrastructure.bedrock.planner import BedrockConversationPlanner
from app.infrastructure.bedrock.response_review import BedrockResponseReviewer
from tests.factories import passage
from tests.test_bedrock_generator import FakeConverseClient

PRIMARY, FALLBACK, REVIEW = (
    "deepseek.v3.2",
    "apac.amazon.nova-lite-v1:0",
    "apac.amazon.nova-lite-v1:0",
)
THROTTLED = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")


class ScriptedClient(FakeConverseClient):
    """Returns one scripted tool input (or raises one error) per request, in order."""

    def __init__(self, tool: str, *script: dict[str, Any] | Exception) -> None:
        super().__init__()
        self.tool, self.script = tool, list(script)

    def converse(self, **request: Any) -> dict[str, Any]:
        step = self.script[min(len(self.requests), len(self.script) - 1)]
        if isinstance(step, Exception):
            self.requests.append(request)
            raise step
        self.tool_input = step
        response = super().converse(**request)
        response["output"]["message"]["content"][0]["toolUse"]["name"] = self.tool
        return response


def planner(client: ScriptedClient) -> BedrockConversationPlanner:
    return BedrockConversationPlanner(client, [PRIMARY, FALLBACK], "ap-southeast-2", [])


def decision(action: str, text: str, **fields: Any) -> dict[str, Any]:
    return {"verdict": "allow", "action": action, "text": text, **fields}


def test_plan_resolves_a_followup_into_one_search_per_subject() -> None:
    client = ScriptedClient(
        "PlanDecision",
        decision(
            "search",
            "How do 1031 and DST deadlines differ?",
            search_queries=["1031 deadlines", "DST deadlines"],
            alternative_query="identification and exchange periods",
        ),
    )
    plan = planner(client).plan("how do they differ?", [])
    assert plan.action == "search" and plan.search_queries == ["1031 deadlines", "DST deadlines"]
    assert plan.model_id == PRIMARY and len(client.requests) == 1


def test_single_subject_is_not_split_into_several_searches() -> None:
    client = ScriptedClient(
        "PlanDecision",
        decision("search", "What are DST risks?", search_queries=["DST fees", "DST liquidity"]),
    )
    assert planner(client).plan("What are the risks?", []).search_queries == ["What are DST risks?"]


@pytest.mark.parametrize(("number", "replacement"), [("741", "721"), ("2017", "2018")])
def test_plan_cannot_silently_replace_an_explicit_number(number: str, replacement: str) -> None:
    question = f"What are the {number} exchange rules?"
    client = ScriptedClient(
        "PlanDecision", decision("search", f"What are the {replacement} exchange rules?")
    )
    plan = planner(client).plan(question, [])
    assert plan.search_queries == [question] and plan.alternative_query == ""


def test_risk_followup_after_a_refusal_keeps_the_previous_single_subject() -> None:
    client = ScriptedClient(
        "PlanDecision",
        decision(
            "search",
            "What are the risks of 1031, DST and 721?",
            search_queries=["1031 risks", "DST risks", "721 risks"],
        ),
    )
    plan = planner(client).plan(
        "Okay, just explain the risks generally",
        [
            {"question": "What about 721 exchanges?", "answer": "A cited answer."},
            {"question": "Should I invest all my savings in this?", "answer": "No."},
        ],
    )
    assert plan.search_queries == ["What are the general risks of 721/UPREIT exchanges?"]


def test_misrouted_capability_question_is_corrected_with_one_reprompt() -> None:
    client = ScriptedClient(
        "PlanDecision",
        decision("search", "1031 exchange rules"),
        decision("help", "I explain the topics covered by the public guides."),
    )
    plan = planner(client).plan("How can you help me?", [])
    assert plan.action == "help" and len(client.requests) == 2


def test_repeated_reply_is_corrected_and_a_second_repeat_fails_closed() -> None:
    earlier = "I can explain 1031 exchanges, DSTs and 721 exchanges using public guides " * 2
    history = [{"question": "Hi", "answer": earlier}]
    client = ScriptedClient("PlanDecision", decision("help", earlier))
    with pytest.raises(SafetyCheckError):
        planner(client).plan("Explain that more simply", history)
    assert len(client.requests) == 2


def test_primary_outage_falls_back_to_the_second_model() -> None:
    client = ScriptedClient("PlanDecision", THROTTLED, decision("clarify", "Which exchange?"))
    plan = planner(client).plan("What about the exchange?", [])
    assert plan.action == "clarify" and plan.model_id == FALLBACK
    assert [r["modelId"] for r in client.requests] == [PRIMARY, FALLBACK]


def test_unusable_plans_fail_closed() -> None:
    with pytest.raises(SafetyCheckError):
        planner(ScriptedClient("PlanDecision", THROTTLED)).plan("Explain DSTs", [])
    with pytest.raises(SafetyCheckError):
        planner(ScriptedClient("PlanDecision", {"verdict": "do_anything"})).plan("Hi there", [])


def _citation(quote: str) -> Citation:
    url = "https://example.com"
    return Citation(1, uuid4(), "irs", "Guide", "IRS", url, url, None, (quote,))


def _review(client: ScriptedClient, claims: int = 1) -> Any:
    quote = "Replacement property must be identified within 45 days."
    reviewer = BedrockResponseReviewer(
        client, REVIEW, "ap-southeast-2", escalation_model_id=PRIMARY
    )
    return reviewer.review(
        "What is the deadline?",
        [VerifiedClaim(f"Claim {i}.", (1,)) for i in range(claims)],
        [_citation(quote)],
        passages=[passage("P1", quote)],
    )


def reviewed(*checks: tuple[bool, str], safety: str = "allow") -> dict[str, Any]:
    return {
        "request_safety": "allow",
        "response_safety": safety,
        "checks": [
            {"index": i, "supported": s, "issue": issue} for i, (s, issue) in enumerate(checks)
        ],
    }


def test_review_reports_failed_claims_and_unsafe_responses() -> None:
    result = _review(
        ScriptedClient(
            "ReviewedResponse", reviewed((True, "none"), (False, "scope_or_qualifiers"))
        ),
        claims=2,
    )
    assert result.failed_claim_indexes == (1,) and result.issue == "scope_or_qualifiers"
    unsafe = _review(
        ScriptedClient("ReviewedResponse", reviewed((True, "none"), safety="misconduct"))
    )
    assert unsafe.safety is SafetyVerdict.MISCONDUCT


def test_incomplete_review_escalates_once_and_never_accepts_unchecked_claims() -> None:
    client = ScriptedClient("ReviewedResponse", reviewed(), reviewed((True, "none")))
    assert _review(client).failed_claim_indexes == ()
    assert [r["modelId"] for r in client.requests] == [REVIEW, PRIMARY]
    with pytest.raises(GenerationError):
        _review(ScriptedClient("ReviewedResponse", reviewed()))


class FlakyEmbeddings:
    def __init__(self, *errors: Exception) -> None:
        self.errors, self.calls = list(errors), 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return [1.0]


def _embedder(fake: FlakyEmbeddings) -> BedrockTitanEmbedder:
    embedder = BedrockTitanEmbedder(FakeConverseClient(), "amazon.titan-embed-text-v2:0", 1024)
    embedder._embeddings = fake  # type: ignore[assignment]
    return embedder


def test_throttled_embedding_is_retried_once_but_other_errors_are_not() -> None:
    fake = FlakyEmbeddings(ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel"))
    assert _embedder(fake).embed_query("q") == [1.0] and fake.calls == 2
    denied = FlakyEmbeddings(ClientError({"Error": {"Code": "AccessDeniedException"}}, "Invoke"))
    with pytest.raises(ClientError):
        _embedder(denied).embed_query("q")
    assert denied.calls == 1


class MeteredClient:
    def converse(self, **_: Any) -> dict[str, Any]:
        return {"usage": {"inputTokens": 100, "outputTokens": 20}}

    def invoke_model(self, **_: Any) -> dict[str, Any]:
        body = json.dumps({"embedding": [0.1], "inputTextTokenCount": 8}).encode()
        return {"body": StreamingBody(io.BytesIO(body), len(body))}


def test_all_sdk_calls_are_metered_without_consuming_embedding_stream() -> None:
    client = MeteredBedrockClient(MeteredClient(), "ap-southeast-2")
    tracker = ActivityTracker(lambda _: None)
    with track_activity(tracker):
        client.converse(modelId="deepseek.v3.2")
        response = client.invoke_model(modelId="amazon.titan-embed-text-v2:0")
    assert json.loads(response["body"].read())["embedding"] == [0.1]
    calls = tracker.usage_snapshot()
    assert [(c["input_tokens"], c["output_tokens"]) for c in calls] == [(100, 20), (8, 0)]
    assert all(c["estimated_cost_usd"] is not None for c in calls)
    assert not any("prompt" in c or "body" in c for c in calls)
