import io
import json
from dataclasses import dataclass, field
from typing import Any

from botocore.response import StreamingBody

from app.application.rag.activity import ActivityTracker, track_activity
from app.application.rag.service import AnswerService
from app.domain.interfaces.conversation import ConversationPlan
from app.domain.interfaces.response_review import ResponseReview
from app.domain.interfaces.safety import SafetyVerdict
from app.domain.models.answers import AnswerReason, AnswerStatus
from app.infrastructure.bedrock.metering import MeteredBedrockClient
from tests.test_answer_service import (
    GROUNDED,
    FakeEmbedder,
    FakeGenerator,
    FakeRetriever,
    _retrieved,
)


@dataclass
class Router:
    calls: list[str] = field(default_factory=list)

    def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan:
        self.calls.append(question)
        raise AssertionError("clear factual or supported rewrite must skip planning")


class Reviewer:
    def review(
        self,
        question: str,
        claims: list[Any],
        citations: list[Any],
        *,
        passages: list[Any],
        reply: str = "",
    ) -> ResponseReview:
        return ResponseReview(SafetyVerdict.ALLOW)


def pipeline(router: Router) -> AnswerService:
    return AnswerService(
        FakeEmbedder(),
        FakeRetriever([_retrieved()]),
        FakeGenerator(GROUNDED),
        top_k=6,
        min_similarity=0.4,
        router=router,
        response_reviewer=Reviewer(),
    )


def test_clear_factual_request_searches_before_generation() -> None:
    router = Router()
    answer = pipeline(router).answer("What is a 1031 exchange?")
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert router.calls == []


def test_clear_app_help_is_written_and_reviewed_without_intent_model() -> None:
    router = Router()
    service = pipeline(router)

    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            return "I explain the rules and risks described in the approved public guides."

    service._responder = Responder()
    answer = service.answer("How can you help me?")
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.reason is AnswerReason.CAPABILITIES
    assert "approved public guides" in (answer.notice or "")
    assert router.calls == []


def test_app_help_drift_uses_scope_fallback_without_document_search() -> None:
    router = Router()
    service = pipeline(router)

    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            return "A 721 exchange defers tax when property is contributed."

    class DriftReviewer(Reviewer):
        def review(
            self,
            question: str,
            claims: list[Any],
            citations: list[Any],
            *,
            passages: list[Any],
            reply: str = "",
        ) -> ResponseReview:
            return ResponseReview(SafetyVerdict.ALLOW, needs_evidence=True)

    service._responder = Responder()
    service._response_reviewer = DriftReviewer()
    answer = service.answer("How can you help me?")
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.reason is AnswerReason.CAPABILITIES
    assert "1031 exchanges" in (answer.notice or "")
    assert service._embedder.calls == 0  # type: ignore[attr-defined]
    assert router.calls == []


def test_simple_rewrite_uses_current_cited_passage_without_embedding() -> None:
    router = Router()
    service = pipeline(router)
    history = [{"question": "What is a 1031 exchange?", "answer": "A cited explanation."}]
    answer = service.answer("Explain it in simpler words", history=history, evidence=[_retrieved()])
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert service._embedder.calls == 0  # type: ignore[attr-defined]
    assert router.calls == []


def test_source_followup_uses_latest_verified_evidence_without_embedding() -> None:
    from app.domain.models.answers import Citation
    from app.domain.policies.conversation_efficiency import evidence_reference_followup

    assert evidence_reference_followup("Which source supports those restrictions?")
    router = Router()
    service = pipeline(router)
    history = [{"question": "What is the deadline?", "answer": "It is 45 days. [1]"}]
    source = _retrieved()
    saved = Citation(
        1,
        source.chunk_id,
        source.document_id,
        source.title,
        source.publisher,
        source.source_url,
        source.source_url,
        source.location,
        (source.content[:50],),
    )
    answer = service.answer(
        "Which source supports those restrictions?",
        history=history,
        evidence=[source],
        source_citations=[saved],
    )
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert service._embedder.calls == 0  # type: ignore[attr-defined]
    assert service._generator.calls == []  # type: ignore[attr-defined]
    assert router.calls == []


def test_topic_change_does_not_reuse_previous_evidence() -> None:
    from app.domain.policies.conversation_efficiency import (
        evidence_only_rewrite,
        help_reply_requires_sources,
    )

    assert not evidence_only_rewrite("What about 721 exchanges?")
    assert not evidence_only_rewrite("Make it shorter and explain the main catch")
    assert not evidence_only_rewrite("No, I meant 741, not 721")
    assert help_reply_requires_sources("A 1031 exchange defers taxes on the sale.")
    assert not help_reply_requires_sources("These public guides cover rules and risks.")


def test_original_personal_request_stays_personal_after_search_rewrite() -> None:
    class RewriteRouter:
        def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan:
            return ConversationPlan(
                action="search",
                query="What is a 1031 exchange?",
                search_queries=["What is a 1031 exchange?"],
                safety_verdict=SafetyVerdict.ALLOW,
            )

    service = pipeline(Router())
    service._router = RewriteRouter()
    answer = service.answer("Should I invest all my savings in a 1031 exchange?")
    assert answer.status is AnswerStatus.REFUSED
    assert answer.reason is AnswerReason.PERSONALIZED_ADVICE


def test_help_with_financial_mechanism_searches_before_returning_answer() -> None:
    class HelpRouter:
        def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan:
            return ConversationPlan(
                action="help",
                reply="A 1031 exchange defers taxes.",
                safety_verdict=SafetyVerdict.ALLOW,
            )

    service = pipeline(Router())
    service._router = HelpRouter()
    answer = service.answer("What information do your guides have?")
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.citations
    assert service._embedder.calls == 3  # type: ignore[attr-defined]


class Client:
    def converse(self, **_: Any) -> dict[str, Any]:
        return {"usage": {"inputTokens": 100, "outputTokens": 20}}

    def invoke_model(self, **_: Any) -> dict[str, Any]:
        body = json.dumps({"embedding": [0.1], "inputTextTokenCount": 8}).encode()
        return {"body": StreamingBody(io.BytesIO(body), len(body))}


def test_all_sdk_calls_are_metered_without_consuming_embedding_stream() -> None:
    client = MeteredBedrockClient(Client(), "ap-southeast-2")
    tracker = ActivityTracker(lambda _: None)
    with track_activity(tracker):
        client.converse(modelId="deepseek.v3.2")
        response = client.invoke_model(modelId="amazon.titan-embed-text-v2:0")
    assert json.loads(response["body"].read())["embedding"] == [0.1]
    calls = tracker.usage_snapshot()
    assert [(c["input_tokens"], c["output_tokens"]) for c in calls] == [(100, 20), (8, 0)]
    assert all(c["estimated_cost_usd"] is not None for c in calls)
    assert not any("prompt" in c or "body" in c for c in calls)


def test_incomplete_claim_review_escalates_once_before_accepting_facts() -> None:
    from uuid import uuid4

    from app.domain.models.answers import Citation, VerifiedClaim
    from app.infrastructure.bedrock.response_review import BedrockResponseReviewer
    from tests.factories import passage
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            self.tool_input = {
                "request_safety": "allow",
                "response_safety": "allow",
                "checks": []
                if request["modelId"].startswith("apac.")
                else [{"index": 0, "supported": True, "issue": "none"}],
                "needs_evidence": False,
            }
            response = super().converse(**request)
            response["output"]["message"]["content"][0]["toolUse"]["name"] = "ReviewedResponse"
            return response

    client = Client()
    reviewer = BedrockResponseReviewer(
        client,
        "apac.amazon.nova-lite-v1:0",
        "ap-southeast-2",
        [],
        help_model_id="deepseek.v3.2",
    )
    quote = "Replacement property must be identified within 45 days."
    citations = [
        Citation(
            1,
            uuid4(),
            "irs",
            "Guide",
            "IRS",
            "https://example.com",
            "https://example.com",
            None,
            (quote,),
        )
    ]
    result = reviewer.review(
        "What is the deadline?",
        [VerifiedClaim("It is 45 days.", (1,))],
        citations,
        passages=[passage("P1", quote)],
    )
    assert result.grounding_issue is None
    assert [r["modelId"] for r in client.requests] == [
        "apac.amazon.nova-lite-v1:0",
        "deepseek.v3.2",
    ]


def test_explicit_app_help_cannot_be_routed_into_a_factual_search() -> None:
    from app.infrastructure.bedrock.efficient_conversation import EfficientConversationRouter
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            self.tool_input = (
                {"verdict": "allow", "action": "search", "text": "1031 exchange rules"}
                if not self.requests
                else {
                    "verdict": "assistant_help",
                    "action": "help",
                    "text": "I explain the topics covered by the public guides.",
                }
            )
            response = super().converse(**request)
            response["output"]["message"]["content"][0]["toolUse"]["name"] = "EfficientDecision"
            return response

    client = Client()
    router = EfficientConversationRouter(client, "deepseek.v3.2", "ap-southeast-2", [])
    plan = router.plan("How can you help me?", [])
    assert plan.action == "help" and len(client.requests) == 2


def test_risk_followup_after_refusal_keeps_the_previous_single_subject() -> None:
    from app.infrastructure.bedrock.efficient_conversation import EfficientConversationRouter
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            self.tool_input = {
                "verdict": "allow",
                "action": "search",
                "text": "What are the risks of 1031, DST and 721?",
                "search_queries": ["1031 risks", "DST risks", "721 risks"],
            }
            response = super().converse(**request)
            response["output"]["message"]["content"][0]["toolUse"]["name"] = "EfficientDecision"
            return response

    router = EfficientConversationRouter(Client(), "deepseek.v3.2", "ap-southeast-2", [])
    plan = router.plan(
        "Okay, just explain the risks generally",
        [
            {"question": "What about 721 exchanges?", "answer": "A cited answer."},
            {"question": "Should I invest all my savings in this?", "answer": "No recommendation."},
        ],
    )
    assert plan.search_queries == ["What are the general risks of 721/UPREIT exchanges?"]
