"""The production pipeline: which path each message takes and how many model calls it costs.

Fakes record every planner, generator, embedding and review call, so each test pins both
the user-visible outcome and the call budget for that kind of message.
"""

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from app.application.rag.service import NOTICE_ACKNOWLEDGED, NOTICE_CAPABILITIES, AnswerService
from app.domain.interfaces.conversation import ConversationPlan
from app.domain.interfaces.generation import GenerationError, GenerationMode, GenerationResult
from app.domain.interfaces.response_review import ResponseReview
from app.domain.interfaces.safety import SafetyCheckError, SafetyVerdict
from app.domain.models.answers import (
    AnswerDraft,
    AnswerReason,
    AnswerStatus,
    Citation,
    LabeledPassage,
)
from app.domain.models.documents import RetrievedPassage
from tests.factories import IDENTIFICATION_TEXT, claim, draft
from tests.test_answer_service import GROUNDED, FakeEmbedder, FakeRetriever, _retrieved

DEADLINES = draft(
    claim(
        "You have 45 days to identify a replacement property.",
        ("P1", "must be identified within 45 days after the date you transfer"),
    ),
    claim(
        "You must receive it within 180 days or by your return due date, if earlier.",
        ("P1", "must be received within 180 days, or by the due date of your tax return"),
    ),
)


@dataclass
class Planner:
    result: ConversationPlan | Exception = field(
        default_factory=lambda: ConversationPlan(action="search", search_queries=["1031 rules"])
    )
    calls: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)

    def plan(self, question: str, history: list[dict[str, str]]) -> ConversationPlan:
        self.calls.append((question, history))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class Generator:
    outputs: list[AnswerDraft | Exception]
    calls: list[tuple[str, list[LabeledPassage], GenerationMode]] = field(default_factory=list)

    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult:
        self.calls.append((question, passages, mode))
        output = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if isinstance(output, Exception):
            raise output
        return GenerationResult(output, "fake-model", None)


@dataclass
class Reviewer:
    reviews: list[ResponseReview | Exception] = field(
        default_factory=lambda: [ResponseReview(SafetyVerdict.ALLOW)]
    )
    calls: list[list[str]] = field(default_factory=list)
    questions: list[str | None] = field(default_factory=list)

    def review(
        self, question: str | None, claims: list[Any], citations: list[Any], *, passages: list[Any]
    ) -> ResponseReview:
        self.calls.append([c.text for c in claims])
        self.questions.append(question)
        review = self.reviews[min(len(self.calls), len(self.reviews)) - 1]
        if isinstance(review, Exception):
            raise review
        return review


@dataclass
class Pipeline:
    planner: Planner = field(default_factory=Planner)
    generator: Generator = field(default_factory=lambda: Generator([GROUNDED]))
    reviewer: Reviewer = field(default_factory=Reviewer)
    embedder: FakeEmbedder = field(default_factory=FakeEmbedder)
    passages: list[RetrievedPassage] = field(default_factory=lambda: [_retrieved()])

    def __post_init__(self) -> None:
        self.service = AnswerService(
            self.embedder,
            FakeRetriever(self.passages),
            self.generator,
            top_k=6,
            min_similarity=0.4,
            timeout_seconds=60.0,  # the production worker budget
            planner=self.planner,
            reviewer=self.reviewer,
        )

    def calls(self) -> tuple[int, int, int, int]:
        """(planner, embedding, generation, review) calls so far."""
        return (
            len(self.planner.calls),
            self.embedder.calls,
            len(self.generator.calls),
            len(self.reviewer.calls),
        )


HISTORY = [
    {
        "question": "What is the 1031 identification deadline?",
        "answer": "Replacement property must be identified within 45 days of the transfer. [1]",
    }
]


def test_planned_factual_turn_costs_one_plan_one_generation_and_one_review() -> None:
    pipeline = Pipeline()
    answer = pipeline.service.answer("And what about the other deadline?", history=HISTORY)
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert pipeline.calls() == (1, 1, 1, 1)


def test_clear_factual_template_skips_planning() -> None:
    pipeline = Pipeline()
    assert pipeline.service.answer("What is a 1031 exchange?").status is AnswerStatus.ANSWERED
    assert pipeline.calls() == (0, 1, 1, 1)


def test_first_capability_question_needs_no_model_but_a_repeat_is_planned() -> None:
    pipeline = Pipeline(
        planner=Planner(ConversationPlan(action="help", reply="Ask about deadlines or risks."))
    )
    first = pipeline.service.answer("How can you help me?")
    assert first.notice == NOTICE_CAPABILITIES and pipeline.calls() == (0, 0, 0, 0)
    history = [{"question": "How can you help me?", "answer": first.render_text()}]
    again = pipeline.service.answer("What can you do?", history=history)
    assert again.notice == "Ask about deadlines or risks."
    assert pipeline.calls() == (1, 0, 0, 0)


@pytest.mark.parametrize("message", ["thanks!", "Thank you so much.", "ok, got it"])
def test_acknowledgement_needs_no_model(message: str) -> None:
    pipeline = Pipeline()
    answer = pipeline.service.answer(message, history=HISTORY)
    assert answer.notice == NOTICE_ACKNOWLEDGED and pipeline.calls() == (0, 0, 0, 0)


def test_help_reply_is_published_without_generation_or_review() -> None:
    reply = "The guides cover deadlines, risks and reporting for each exchange type."
    pipeline = Pipeline(planner=Planner(ConversationPlan(action="help", reply=reply)))
    answer = pipeline.service.answer("What kind of guides do you have?")
    assert answer.reason is AnswerReason.CAPABILITIES and answer.notice == reply
    assert pipeline.calls() == (1, 0, 0, 0)


def test_help_reply_that_teaches_mechanics_is_answered_from_documents_instead() -> None:
    reply = "A 1031 exchange lets you defer taxes when you swap one investment property."
    pipeline = Pipeline(planner=Planner(ConversationPlan(action="help", reply=reply)))
    answer = pipeline.service.answer("What do these 1031 documents mean in easy words?")
    assert answer.status is AnswerStatus.ANSWERED and answer.citations
    assert "what these guides are about" in pipeline.generator.calls[0][0]
    assert pipeline.calls() == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (SafetyVerdict.MISCONDUCT, AnswerReason.SAFETY_VIOLATION),
        (SafetyVerdict.HARMFUL, AnswerReason.SAFETY_VIOLATION),
        (SafetyVerdict.DISCRIMINATION, AnswerReason.SAFETY_VIOLATION),
        (SafetyVerdict.INJECTION, AnswerReason.PROMPT_INJECTION),
        (SafetyVerdict.PERSONALIZED_ADVICE, AnswerReason.PERSONALIZED_ADVICE),
    ],
)
def test_semantic_refusals_stop_before_retrieval(
    verdict: SafetyVerdict, reason: AnswerReason
) -> None:
    plan = ConversationPlan(action="search", search_queries=["q"], safety_verdict=verdict)
    pipeline = Pipeline(planner=Planner(plan))
    answer = pipeline.service.answer("A request without an obvious forbidden keyword")
    assert answer.status is AnswerStatus.REFUSED and answer.reason is reason
    assert pipeline.calls() == (1, 0, 0, 0)


def test_planner_scope_guess_cannot_veto_a_domain_question() -> None:
    plan = ConversationPlan(
        action="search",
        search_queries=["1031 identification period"],
        safety_verdict=SafetyVerdict.OUT_OF_SCOPE,
    )
    pipeline = Pipeline(planner=Planner(plan))
    assert pipeline.service.answer("Explain the identification period").status is (
        AnswerStatus.ANSWERED
    )
    unrelated = replace(plan, search_queries=["tomorrow's forecast"])
    pipeline = Pipeline(planner=Planner(unrelated))
    assert pipeline.service.answer("Will it be sunny?").reason is AnswerReason.OUT_OF_SCOPE


def test_planner_outage_fails_closed() -> None:
    pipeline = Pipeline(planner=Planner(SafetyCheckError("unavailable")))
    answer = pipeline.service.answer("Tell me about replacement property")
    assert answer.reason is AnswerReason.SAFETY_CHECK_UNAVAILABLE
    assert pipeline.calls() == (1, 0, 0, 0)


def test_personally_framed_question_skips_planning_and_is_never_advice() -> None:
    pipeline = Pipeline()
    answer = pipeline.service.answer("Should I report the exchange on Form 8824?")
    assert answer.status is AnswerStatus.REFUSED
    assert answer.reason is AnswerReason.PERSONALIZED_ADVICE
    assert answer.explanation  # general education alongside the refusal
    assert pipeline.generator.calls[0][2] is GenerationMode.GENERAL_EDUCATION
    assert pipeline.calls() == (0, 1, 1, 1)


@pytest.mark.parametrize(
    "question", ["Should I invest 1M dollars?", "Which DST should I buy?", "Am I accredited?"]
)
def test_personal_decision_is_refused_at_the_guard(question: str) -> None:
    pipeline = Pipeline()
    answer = pipeline.service.answer(question)
    assert answer.reason is AnswerReason.PERSONALIZED_ADVICE
    assert pipeline.calls() == (0, 0, 0, 0)


def test_simplification_reuses_cited_evidence_without_planning_or_search() -> None:
    simpler = draft(
        claim(
            "You get 45 days to pick the new property.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    pipeline = Pipeline(generator=Generator([simpler]))
    answer = pipeline.service.answer(
        "Explain it in simpler words", history=HISTORY, evidence=[_retrieved()]
    )
    assert "45 days to pick" in answer.render_text()
    assert pipeline.calls() == (0, 0, 1, 1)
    assert "<previous_answer>" in pipeline.generator.calls[0][0]


def test_simplification_that_copies_the_previous_answer_is_rewritten_once() -> None:
    copied = GROUNDED  # identical to the previous answer in HISTORY
    simpler = draft(
        claim(
            "You get 45 days to pick the new property.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    pipeline = Pipeline(generator=Generator([copied, simpler]))
    answer = pipeline.service.answer(
        "Explain it in simpler words", history=HISTORY, evidence=[_retrieved()]
    )
    assert "45 days to pick" in answer.render_text()
    assert pipeline.calls() == (0, 0, 2, 1)  # the copy is never reviewed or shown
    assert "repeated the previous answer" in pipeline.generator.calls[1][0]


def test_simplification_with_misquoted_evidence_gets_one_regeneration() -> None:
    misquoted = draft(claim("You get 45 days to pick the new property.", ("P1", "45 days to pick")))
    simpler = draft(
        claim(
            "You get 45 days to pick the new property.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    pipeline = Pipeline(generator=Generator([misquoted, simpler]))
    answer = pipeline.service.answer("make it shorter", history=HISTORY, evidence=[_retrieved()])
    assert answer.status is AnswerStatus.ANSWERED
    assert pipeline.calls() == (0, 0, 2, 1)
    assert "word for word" in pipeline.generator.calls[1][0]


def test_source_followup_is_answered_from_saved_citations_without_models() -> None:
    evidence = replace(
        _retrieved(), content=IDENTIFICATION_TEXT.replace(" within 45", "\nwithin 45")
    )
    quote = "must be identified within 45 days after the date you transfer"
    citation = Citation(
        1,
        evidence.chunk_id,
        evidence.document_id,
        evidence.title,
        evidence.publisher,
        evidence.source_url,
        evidence.source_url,
        None,
        (quote,),
    )
    pipeline = Pipeline()
    answer = pipeline.service.answer(
        "Which source supports that?",
        history=HISTORY,
        evidence=[evidence],
        source_citations=[citation],
    )
    # The saved quote spans a PDF line break; matching must tolerate that whitespace.
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.explanation[0].text.startswith("The previous answer drew on")
    assert pipeline.calls() == (0, 0, 0, 0)


def test_new_question_in_a_thread_does_not_reuse_previous_evidence() -> None:
    pipeline = Pipeline()
    pipeline.service.answer(
        "Now switch to DST trustee restrictions", history=HISTORY, evidence=[_retrieved()]
    )
    assert pipeline.calls() == (1, 1, 1, 1)


def test_review_rejecting_a_minor_claim_publishes_the_rest_without_regenerating() -> None:
    fuller = DEADLINES.model_copy(
        update={
            "explanation": [
                *DEADLINES.explanation,
                claim(
                    "Both periods start on the day you transfer the old property.",
                    ("P1", "within 45 days after the date you transfer"),
                ),
            ]
        }
    )
    reviewer = Reviewer([ResponseReview(SafetyVerdict.ALLOW, failed_claim_indexes=(2,))])
    pipeline = Pipeline(generator=Generator([fuller]), reviewer=reviewer)
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert [c.text for c in answer.explanation] == [c.text for c in DEADLINES.explanation]
    assert answer.dropped_claims == 1 and [c.number for c in answer.citations] == [1]
    assert pipeline.calls() == (0, 1, 1, 1)


def test_review_gutting_the_explanation_gets_one_repair_instead_of_fragments() -> None:
    reviewer = Reviewer(
        [
            ResponseReview(SafetyVerdict.ALLOW, failed_claim_indexes=(0,), issue="scope"),
            ResponseReview(SafetyVerdict.ALLOW),
        ]
    )
    pipeline = Pipeline(generator=Generator([DEADLINES, DEADLINES]), reviewer=reviewer)
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert len(answer.explanation) == 2  # the repaired draft, not the one-claim remainder
    assert pipeline.calls() == (0, 1, 2, 2)


def test_review_rejecting_every_claim_gets_one_repair_then_stays_unsupported() -> None:
    rejected = ResponseReview(
        SafetyVerdict.ALLOW, failed_claim_indexes=(0,), issue="numbers_or_conditions"
    )
    pipeline = Pipeline(reviewer=Reviewer([rejected]))
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert answer.status is AnswerStatus.UNSUPPORTED
    assert answer.reason is AnswerReason.CITATIONS_FAILED_VERIFICATION
    assert answer.explanation == [] and answer.citations == []
    assert pipeline.calls() == (0, 1, 2, 2)
    assert "numbers_or_conditions" in pipeline.generator.calls[1][0]


def test_repaired_answer_is_published_only_after_it_passes_review() -> None:
    reviewer = Reviewer(
        [
            ResponseReview(SafetyVerdict.ALLOW, failed_claim_indexes=(0,), issue="scope"),
            ResponseReview(SafetyVerdict.ALLOW),
        ]
    )
    pipeline = Pipeline(reviewer=reviewer)
    assert pipeline.service.answer("What is a 1031 exchange?").status is AnswerStatus.ANSWERED
    assert pipeline.calls() == (0, 1, 2, 2)


@pytest.mark.parametrize(
    "review", [ResponseReview(SafetyVerdict.MISCONDUCT), GenerationError("unavailable")]
)
def test_unsafe_or_unreviewed_answers_are_never_published(
    review: ResponseReview | Exception,
) -> None:
    pipeline = Pipeline(reviewer=Reviewer([review]))
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert answer.status in (AnswerStatus.REFUSED, AnswerStatus.FAILED)
    assert answer.explanation == [] and answer.citations == []


def test_unanswerable_passages_get_exactly_one_search_refinement() -> None:
    plan = ConversationPlan(
        action="search",
        search_queries=["1031 deadline extension"],
        alternative_query="tax return due date and the exchange period",
    )
    pipeline = Pipeline(
        planner=Planner(plan),
        generator=Generator([draft(answerable=False), draft(answerable=False)]),
    )
    answer = pipeline.service.answer("Does an extension change it?", history=HISTORY)
    assert answer.reason is AnswerReason.MODEL_FOUND_INSUFFICIENT
    assert pipeline.calls() == (1, 2, 2, 0)


def test_generator_always_sees_the_original_request_with_its_style() -> None:
    plan = ConversationPlan(action="search", search_queries=["1031 identification deadline"])
    pipeline = Pipeline(planner=Planner(plan))
    pipeline.service.answer("Explain that deadline like I'm twelve", history=HISTORY)
    task = pipeline.generator.calls[0][0]
    assert task.startswith("Explain that deadline like I'm twelve")
    assert "1031 identification deadline" in task


def test_each_subject_keeps_its_best_passage_before_extra_passages() -> None:
    class Search:
        calls = 0

        def search(
            self, vector: list[float], *, top_k: int, min_similarity: float
        ) -> list[RetrievedPassage]:
            self.calls += 1
            return [replace(_retrieved(), document_id=f"topic-{self.calls}") for _ in range(top_k)]

    plan = ConversationPlan(action="search", search_queries=["1031", "DST", "721"])
    pipeline = Pipeline(planner=Planner(plan))
    pipeline.service._retriever = Search()
    pipeline.service.answer("Explain all three in easy words")
    selected = pipeline.generator.calls[0][1]
    assert {p.document_id for p in selected[:3]} == {"topic-1", "topic-2", "topic-3"}


def test_no_retry_is_started_without_time_to_finish_it() -> None:
    rejected = ResponseReview(
        SafetyVerdict.ALLOW, failed_claim_indexes=(0,), issue="numbers_or_conditions"
    )
    pipeline = Pipeline(reviewer=Reviewer([rejected]))
    pipeline.service._timeout_seconds = 5.0  # less than one more draft needs
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert answer.status is AnswerStatus.UNSUPPORTED  # an honest outcome, not an error
    assert pipeline.calls() == (0, 1, 1, 1)


def test_unsupported_first_draft_skips_refinement_when_time_is_short() -> None:
    plan = ConversationPlan(
        action="search",
        search_queries=["1031 deadline extension"],
        alternative_query="tax return due date and the exchange period",
    )
    pipeline = Pipeline(planner=Planner(plan), generator=Generator([draft(answerable=False)]))
    pipeline.service._timeout_seconds = 5.0
    answer = pipeline.service.answer("Does an extension change it?", history=HISTORY)
    assert answer.reason is AnswerReason.MODEL_FOUND_INSUFFICIENT
    assert pipeline.calls() == (1, 1, 1, 0)


def test_followup_writer_sees_the_previous_answer_so_it_can_build_on_it() -> None:
    pipeline = Pipeline()
    pipeline.service.answer("What is a 1031 exchange?")
    assert "<previous_answer>" not in pipeline.generator.calls[0][0]
    pipeline.service.answer("How does the whole process work, in detail?", history=HISTORY)
    task = pipeline.generator.calls[1][0]
    assert task.startswith("How does the whole process work, in detail?")
    assert "Do not restate it" in task and HISTORY[0]["answer"][:40] in task


@pytest.mark.parametrize(
    "message", ["I didn't understand that. Can you explain more?", "I still don't get it"]
)
def test_confused_reader_gets_a_different_explanation_from_the_same_sources(message: str) -> None:
    clearer = draft(
        claim(
            "Picture selling a rental home: from the transfer date you get 45 days to name "
            "the replacement property.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    pipeline = Pipeline(generator=Generator([clearer]))
    history = [{"question": "What is a 1031 exchange?", "answer": "A first explanation."}, *HISTORY]
    answer = pipeline.service.answer(message, history=history, evidence=[_retrieved()])
    assert answer.status is AnswerStatus.ANSWERED
    assert pipeline.calls() == (0, 0, 1, 1)  # no planning, no new search
    task = pipeline.generator.calls[0][0]
    assert "different approach" in task and task.count("<earlier_answer>") == 2


def test_confusion_about_a_named_subject_is_a_new_question() -> None:
    pipeline = Pipeline()
    pipeline.service.answer(
        "I don't understand what a DST is", history=HISTORY, evidence=[_retrieved()]
    )
    assert pipeline.calls() == (1, 1, 1, 1)


def test_confused_reader_never_gets_an_earlier_explanation_back() -> None:
    reworded = draft(
        claim(
            "Replacement property must be identified within 45 days of your transfer.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    clearer = draft(
        claim(
            "Picture selling a rental home: from the transfer date you get 45 days to name "
            "the replacement property.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        )
    )
    pipeline = Pipeline(generator=Generator([reworded, clearer]))
    history = [*HISTORY, {"question": "I don't get it", "answer": "Something different."}]
    answer = pipeline.service.answer(
        "I still don't get it", history=history, evidence=[_retrieved()]
    )
    assert "Picture selling" in answer.render_text()
    assert pipeline.calls() == (0, 0, 2, 1)


def test_trimmed_answer_never_opens_mid_thought() -> None:
    answer_draft = draft(
        claim(
            "The replacement property must still be identified within 45 days.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        ),
        claim(
            "However, the replacement property must be received within 180 days.",
            ("P1", "must be received within 180 days, or by the due date of your tax return"),
        ),
        claim(
            "That period ends earlier if your tax return is due first.",
            ("P1", "by the due date of your tax return, whichever is earlier"),
        ),
    )
    reviewer = Reviewer([ResponseReview(SafetyVerdict.ALLOW, failed_claim_indexes=(0,))])
    pipeline = Pipeline(generator=Generator([answer_draft]), reviewer=reviewer)
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert answer.explanation[0].text.startswith("The replacement property must be received")


def test_a_thin_search_never_shrinks_the_passages_for_the_other_subjects() -> None:
    class Search:
        calls = 0

        def search(
            self, vector: list[float], *, top_k: int, min_similarity: float
        ) -> list[RetrievedPassage]:
            self.calls += 1
            found = 1 if self.calls == 1 else top_k
            return [replace(_retrieved(), document_id=f"q{self.calls}") for _ in range(found)]

    plan = ConversationPlan(action="search", search_queries=["DST trustees", "DST powers"])
    pipeline = Pipeline(planner=Planner(plan))
    pipeline.service._retriever = Search()
    pipeline.service.answer("What can DST trustees not do?")
    selected = [p.document_id for p in pipeline.generator.calls[0][1]]
    assert len(selected) == 6 and selected[:2] == ["q1", "q2"]


def test_draft_gutted_by_the_quote_check_is_regenerated_without_a_wasted_review() -> None:
    misquoted = draft(
        claim("A 1031 swap delays the tax bill.", ("P1", "a sentence not in the passage")),
        claim("You trade one building for another.", ("P1", "another invented quotation here")),
        claim(
            "Replacement property must be identified within 45 days.",
            ("P1", "must be identified within 45 days after the date you transfer"),
        ),
    )
    pipeline = Pipeline(generator=Generator([misquoted, DEADLINES]))
    answer = pipeline.service.answer("What is a 1031 exchange?")
    assert len(answer.explanation) == 2  # the repaired draft
    assert pipeline.calls() == (0, 1, 2, 1)  # only the repaired draft is reviewed
    assert "A 1031 swap delays the tax bill." in pipeline.generator.calls[1][0]


def test_a_request_the_planner_screened_is_not_re_judged_by_the_reviewer() -> None:
    second_opinion = ResponseReview(SafetyVerdict.ALLOW, request_safety=SafetyVerdict.MISCONDUCT)
    planned = ConversationPlan(action="search", search_queries=["1031 tax deferral"], model_id="m")
    pipeline = Pipeline(planner=Planner(planned), reviewer=Reviewer([second_opinion]))
    answer = pipeline.service.answer("How can I legally avoid tax on my property sale?")
    assert answer.status is AnswerStatus.ANSWERED
    assert pipeline.reviewer.questions == [None]  # the reviewer never saw the request
    # Fast paths skip the planner, so there the reviewer's request verdict still applies.
    pipeline = Pipeline(reviewer=Reviewer([second_opinion]))
    assert pipeline.service.answer("What is a 1031 exchange?").reason is (
        AnswerReason.SAFETY_VIOLATION
    )
