from dataclasses import dataclass, field
from typing import Any

import pytest

from app.application.rag.activity import ActivityTracker, track_activity
from app.application.rag.service import AnswerService
from app.domain.interfaces.conversation import ConversationPlan
from app.domain.interfaces.generation import GenerationMode, GenerationResult
from app.domain.interfaces.safety import SafetyCheckError, SafetyVerdict
from app.domain.models.answers import AnswerReason, AnswerStatus, LabeledPassage
from app.domain.models.documents import RetrievedPassage
from tests.test_answer_service import (
    GROUNDED,
    FakeEmbedder,
    FakeGenerator,
    FakeRetriever,
    _retrieved,
)


@dataclass
class Router:
    output: ConversationPlan | Exception
    calls: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)

    def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan:
        self.calls.append((question, history))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@dataclass
class Gate:
    verdict: SafetyVerdict = SafetyVerdict.ALLOW
    output: SafetyVerdict = SafetyVerdict.ALLOW
    calls: list[str] = field(default_factory=list)

    def check(self, text: str) -> SafetyVerdict:
        self.calls.append(text)
        return self.verdict

    def check_output(self, text: str) -> SafetyVerdict:
        return self.output


def service(router: Router, gate: Gate | None = None) -> AnswerService:
    return AnswerService(
        FakeEmbedder(),
        FakeRetriever([_retrieved()]),
        FakeGenerator(GROUNDED),
        top_k=6,
        min_similarity=0.4,
        router=router,
        safety_gate=gate or Gate(),
    )


@pytest.mark.parametrize(
    "question",
    [
        "hi what you do",
        "What kind of taxes do you know about?",
        "Can you explain what kind of data you have?",
    ],
)
def test_metadata_gets_semantic_reply_without_document_search(question: str) -> None:
    router = Router(
        ConversationPlan(
            action="help",
            query="",
            alternative_query="",
            reply="I explain real-estate exchanges using approved public sources.",
        )
    )
    answer = service(router).answer(question)
    assert answer.status is AnswerStatus.ANSWERED
    assert isinstance(router.output, ConversationPlan)
    assert answer.notice == router.output.reply
    assert not answer.citations and answer.retrieved_passages == 0


def test_follow_up_resolved_using_history_and_original_intent_checked() -> None:
    router = Router(
        ConversationPlan(
            action="search",
            query="What are the 1031 exchange identification deadlines?",
            alternative_query="1031 replacement property identification period",
            reply="",
        )
    )
    gate = Gate()
    history = [{"question": "Explain 1031 exchanges", "answer": "A real estate exchange."}]
    answer = service(router, gate).answer("What are the deadlines?", history=history)
    assert router.calls == [("What are the deadlines?", history)]
    assert gate.calls[0] == "What are the deadlines?"
    assert answer.citations and answer.status is AnswerStatus.ANSWERED


def test_ambiguous_section_gets_clarification_without_invented_evidence() -> None:
    answer = service(
        Router(
            ConversationPlan(
                action="clarify",
                query="",
                alternative_query="",
                reply="Do you mean Section 741, or an exchange topic we discussed earlier?",
            )
        )
    ).answer("What are 741 rules?")
    assert answer.reason is AnswerReason.CLARIFICATION
    assert not answer.citations and not answer.explanation


def test_one_search_refinement_preserves_floor_and_is_reported() -> None:
    class Search:
        calls = 0

        def search(
            self, vector: list[float], *, top_k: int, min_similarity: float
        ) -> list[RetrievedPassage]:
            self.calls += 1
            assert min_similarity == 0.4
            return [] if self.calls == 1 else [_retrieved()]

    router = Router(
        ConversationPlan(
            action="search",
            query="1031 deadlines",
            alternative_query="1031 identification period",
            reply="",
        )
    )
    search = Search()
    pipeline = service(router)
    pipeline._retriever = search
    tracker = ActivityTracker(lambda _: None)
    with track_activity(tracker):
        answer = pipeline.answer("When must I identify replacement property in a 1031?")
    assert answer.status is AnswerStatus.ANSWERED and search.calls == 2
    assert any(s.kind == "search_refinement" for s in tracker.steps)


def test_router_failure_does_not_guess() -> None:
    answer = service(Router(SafetyCheckError("unavailable"))).answer("What can you explain?")
    assert answer.reason is AnswerReason.SAFETY_CHECK_UNAVAILABLE


def test_semantic_misconduct_cannot_be_sanitized_by_router() -> None:
    router = Router(
        ConversationPlan(
            action="search",
            query="1031 reporting obligations",
            alternative_query="",
            reply="",
        )
    )
    gate = Gate(verdict=SafetyVerdict.MISCONDUCT)
    question = "How can I make the IRS never discover my property profit?"
    answer = service(router, gate).answer(question)
    assert question in gate.calls[0]
    assert answer.reason is AnswerReason.SAFETY_VIOLATION and not answer.citations


def test_generated_help_is_reviewed_before_display() -> None:
    answer = service(
        Router(
            ConversationPlan(
                action="help",
                query="",
                alternative_query="",
                reply="An unsafe draft.",
            )
        ),
        Gate(output=SafetyVerdict.MISCONDUCT),
    ).answer("What can you do?")
    assert answer.reason is AnswerReason.SAFETY_VIOLATION
    assert answer.notice != "An unsafe draft."


def test_unanswerable_first_passages_trigger_only_one_more_search() -> None:
    from app.domain.models.answers import AnswerDraft

    class Generator(FakeGenerator):
        def generate(
            self, question: str, passages: list[LabeledPassage], mode: GenerationMode
        ) -> GenerationResult:
            if not self.calls:
                self.calls.append((question, passages, mode))
                return GenerationResult(AnswerDraft(answerable=False), "fake-model", None)
            return super().generate(question, passages, mode)

    pipeline = service(
        Router(
            ConversationPlan(
                action="search",
                query="1031 deadlines",
                alternative_query="1031 identification period",
            )
        )
    )
    generator = Generator(GROUNDED)
    pipeline._generator = generator
    answer = pipeline.answer("What are the 1031 deadlines?")
    assert answer.status is AnswerStatus.ANSWERED and len(generator.calls) == 2
    assert answer.citations


def test_missing_irrelevant_plan_fields_are_allowed_but_missing_content_is_rejected() -> None:
    from app.infrastructure.bedrock.conversation import BedrockConversationRouter
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            result = super().converse(**request)
            result["output"]["message"]["content"][0]["toolUse"]["name"] = "ConversationDecision"
            return result

    good = BedrockConversationRouter(
        Client({"action": "clarify", "text": "Which exchange do you mean?"}),
        "apac.amazon.nova-lite-v1:0",
        "ap-southeast-2",
        [],
    )
    assert good.plan("What exchanges?", []).action == "clarify"
    bad = BedrockConversationRouter(
        Client({"action": "search"}),
        "apac.amazon.nova-lite-v1:0",
        "ap-southeast-2",
        [],
    )
    with pytest.raises(SafetyCheckError):
        bad.plan("What exchanges?", [])


def test_router_cannot_turn_an_unrelated_request_into_supported_education() -> None:
    router = Router(
        ConversationPlan(
            action="search",
            query="How do 1031 exchanges defer gains?",
            alternative_query="1031 tax deferral",
        )
    )
    answer = service(router).answer("How do qualified opportunity zone funds defer gains?")
    assert answer.reason is AnswerReason.OUT_OF_SCOPE and not answer.citations
    help_router = Router(ConversationPlan(action="help", reply="I can explain 1031 exchanges."))
    answer = service(help_router, Gate(verdict=SafetyVerdict.OUT_OF_SCOPE)).answer(
        "Give me a banana bread recipe"
    )
    assert answer.reason is AnswerReason.OUT_OF_SCOPE


def test_composed_reply_is_distinct_from_routing_text_and_is_safety_checked() -> None:
    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            assert question == "Explain your document collection in easy words"
            return "These are public guides about property-investment rules, risks and paperwork."

    pipeline = service(
        Router(ConversationPlan(action="help", reply="Routing intent, not an answer."))
    )
    pipeline._responder = Responder()
    answer = pipeline.answer("Explain your document collection in easy words")
    assert answer.model_id == "reply-model"
    assert answer.notice is not None and answer.notice.startswith("These are public guides")
    assert not answer.citations
    pipeline._safety_gate = Gate(output=SafetyVerdict.MISCONDUCT)
    assert (
        pipeline.answer("Explain your document collection in easy words").reason
        is AnswerReason.SAFETY_VIOLATION
    )


def test_conversation_writer_receives_real_turns_and_retries_a_repeated_draft() -> None:
    from app.infrastructure.bedrock.conversation import BedrockConversationResponder
    from tests.test_bedrock_generator import FakeConverseClient

    old = (
        "I can provide information about property investment documents and the public rules "
        "and guidance that apply to real estate exchanges, deadlines, risks and reporting."
    )
    revised = (
        "These guides explain the rules and paperwork people need to understand "
        "when changing property investments."
    )

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            self.tool_input = {"text": old if not self.requests else revised}
            result = super().converse(**request)
            result["output"]["message"]["content"][0]["toolUse"]["name"] = "ConversationReply"
            return result

    client = Client()
    responder = BedrockConversationResponder(client, "deepseek.v3.2", "ap-southeast-2", [])
    text = responder.respond(
        "Can you say that more simply?", [{"question": "What data do you have?", "answer": old}]
    )
    assert text == revised and len(client.requests) == 2
    assert [x["role"] for x in client.requests[0]["messages"]] == ["user", "assistant", "user"]


@pytest.mark.parametrize("number,replacement", [("741", "721"), ("2027", "2026")])
def test_router_cannot_silently_replace_explicit_numbers(number: str, replacement: str) -> None:
    from app.infrastructure.bedrock.conversation import BedrockConversationRouter
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            self.tool_input = (
                {"action": "search", "text": f"Explain {replacement} rules"}
                if not self.requests
                else {
                    "action": "clarify",
                    "text": f"Can you confirm which rules you mean by {number}?",
                }
            )
            result = super().converse(**request)
            result["output"]["message"]["content"][0]["toolUse"]["name"] = "ConversationDecision"
            return result

    client = Client()
    plan = BedrockConversationRouter(client, "deepseek.v3.2", "ap-southeast-2", []).plan(
        f"Explain {number} rules", []
    )
    assert plan.action == "clarify" and number in plan.reply and len(client.requests) == 2


@pytest.mark.parametrize("omit_queries", [False, True])
def test_app_reply_quality_review_requests_evidence_for_financial_claims(
    omit_queries: bool,
) -> None:
    from app.infrastructure.bedrock.conversation import BedrockConversationResponder
    from tests.test_bedrock_generator import FakeConverseClient

    class Client(FakeConverseClient):
        def converse(self, **request: Any) -> dict[str, Any]:
            written = sum(x["modelId"] == "deepseek.v3.2" for x in self.requests)
            writer = request["modelId"] == "deepseek.v3.2"
            if writer:
                self.tool_input = {
                    "text": "A 1031 strategy guarantees a better property."
                    if not written
                    else "These public guides cover rules, risks and paperwork."
                }
            else:
                self.tool_input = {"verdict": "needs_evidence" if written == 1 else "metadata_only"}
                if not omit_queries:
                    self.tool_input["search_queries"] = (
                        ["1031 exchange rules"] if written == 1 else []
                    )
            result = super().converse(**request)
            result["output"]["message"]["content"][0]["toolUse"]["name"] = (
                "ConversationReply" if writer else "ReplyReview"
            )
            return result

    client = Client()
    responder = BedrockConversationResponder(
        client,
        "deepseek.v3.2",
        "ap-southeast-2",
        [],
        review_model_id="apac.amazon.nova-lite-v1:0",
    )
    from app.domain.interfaces.conversation import ConversationNeedsEvidence

    with pytest.raises(ConversationNeedsEvidence) as raised:
        responder.respond("What kind of information do you have?", [])
    assert raised.value.queries == ["1031 exchange rules and requirements"]
    assert len(client.requests) == 2


def test_app_explanation_hands_factual_content_to_retrieval_with_citations() -> None:
    from app.domain.interfaces.conversation import ConversationNeedsEvidence

    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            raise ConversationNeedsEvidence(
                ["1031 identification period", "1031 completion period"]
            )

    pipeline = service(Router(ConversationPlan(action="help", reply="Explain the guides simply")))
    pipeline._responder = Responder()
    answer = pipeline.answer("What do these guides mean in easy words?")
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert not answer.notice
    assert pipeline._embedder.calls == 2  # type: ignore[attr-defined]


def test_evidence_handoff_cannot_silently_change_explicit_numeric_reference() -> None:
    from app.domain.interfaces.conversation import ConversationNeedsEvidence

    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            raise ConversationNeedsEvidence(["721 exchange rules"])

    generator = FakeGenerator(GROUNDED)
    pipeline = service(Router(ConversationPlan(action="help", reply="Explain source coverage")))
    pipeline._responder = Responder()
    pipeline._generator = generator
    pipeline.answer("What information do you have about 741?")
    assert "741" in generator.calls[0][0] and "721" not in generator.calls[0][0]


def test_multi_subject_context_keeps_each_subject_before_extra_passages() -> None:
    from dataclasses import replace

    from app.domain.interfaces.conversation import ConversationNeedsEvidence
    from tests.factories import IDENTIFICATION_TEXT

    class Responder:
        model_id = "reply-model"

        def respond(self, question: str, history: list[dict[str, str]]) -> str:
            raise ConversationNeedsEvidence(["1031 rules", "DST rules", "721 rules"])

    class Search:
        calls = 0

        def search(
            self, vector: list[float], *, top_k: int, min_similarity: float
        ) -> list[RetrievedPassage]:
            self.calls += 1
            return [
                replace(
                    _retrieved(),
                    document_id=f"topic-{self.calls}",
                    content=IDENTIFICATION_TEXT + " filler" * 240,
                )
                for _ in range(top_k)
            ]

    pipeline = service(Router(ConversationPlan(action="help", reply="Explain the three subjects")))
    pipeline._responder = Responder()
    pipeline._retriever = Search()
    generator = FakeGenerator(GROUNDED)
    pipeline._generator = generator
    pipeline.answer("Explain all of those topics in easy words")
    selected = generator.calls[0][1]
    assert {p.document_id for p in selected[:3]} == {"topic-1", "topic-2", "topic-3"}
