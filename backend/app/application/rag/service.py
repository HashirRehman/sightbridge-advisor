"""Grounded question answering: a fixed, bounded pipeline with no agent loop.

policy → embed → retrieve (similarity floor) → generate (structured) → verify citations →
answer + disclaimer. Every branch returns an AssistantAnswer with a status and disclaimer; no
exception escapes for expected failures.
"""

import logging

from app.application.rag.activity import activity_step
from app.application.rag.budget import answer_budget
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator, GenerationError, GenerationMode
from app.domain.interfaces.retrieval import PassageRetriever
from app.domain.interfaces.safety import SafetyCheckError, SafetyGate, SafetyVerdict
from app.domain.models.answers import (
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    LabeledPassage,
)
from app.domain.models.documents import RetrievedPassage
from app.domain.policies.citation_verifier import verify_answer
from app.domain.policies.question_policy import PolicyOutcome, evaluate_question
from app.domain.policies.retrieval_query import expand_retrieval_query
from app.domain.policies.safety import local_safety_verdict

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 9000

NOTICE_CAPABILITIES = (
    "I can help you understand 1031 exchanges, Delaware Statutory Trusts (DSTs), and "
    "721/UPREIT exchanges in plain language. Ask me how they work, what deadlines apply, "
    "what risks to understand, or how they compare. "
    'For example: "How does a 1031 exchange work?" '
    "I'll explain the key points here and include sources you can check."
)
NOTICE_ROLE = (
    "My job is to help you understand 1031 exchanges, Delaware Statutory Trusts (DSTs), "
    "and 721/UPREIT exchanges. I explain how they work, their key rules and risks, and how "
    "they compare, using the approved source documents. You can read the explanation "
    "here and use the citations to check the details."
)

NOTICE_PERSONALIZED = (
    "I can't make recommendations or assess a specific person's situation. That requires a "
    "qualified tax, legal or financial professional who knows the full facts."
)
NOTICE_PERSONALIZED_WITH_EDUCATION = (
    f"{NOTICE_PERSONALIZED} Here is general educational information on the topics your "
    "question touches on:"
)
NOTICE_OUT_OF_SCOPE = (
    "I can only answer educational questions about 1031 exchanges, Delaware Statutory Trusts "
    "(DSTs) and 721 exchanges into REIT operating partnerships, using a curated set of public "
    "sources. This question is outside that scope."
)
NOTICE_INJECTION = (
    "I can't change how I operate or reveal my instructions. I can answer educational "
    "questions about 1031 exchanges, DSTs and 721 exchanges."
)
NOTICE_UNSUPPORTED = (
    "I couldn't find support for an answer in the approved source documents, so I won't "
    "guess. Try rephrasing, or ask about a related 1031, DST or 721 topic."
)
NOTICE_PRIVACY = (
    "Please remove private details such as passwords, account numbers, government IDs or "
    "contact information, then ask again. I don't need those details to explain the general rules."
)
NOTICE_SUPPORT = (
    "Your safety matters. If you might hurt yourself or are in immediate danger, contact "
    "local emergency services now. You can also reach out to someone you trust and ask them "
    "to stay with you while you get support."
)
NOTICE_SAFETY = (
    "I can't help with harmful, deceptive or discriminatory actions. I can explain the general "
    "rules, compliance requirements and risks using the approved sources."
)
NOTICE_SAFETY_UNAVAILABLE = (
    "I couldn't complete the request checks, so I haven't generated an answer. "
    "Please try again in a moment."
)

NOTICE_FAILED = (
    "Something went wrong while preparing an answer, so none was produced. Please try again "
    "in a moment."
)


class AnswerService:
    def __init__(
        self,
        embedder: Embedder,
        retriever: PassageRetriever,
        generator: AnswerGenerator,
        *,
        top_k: int,
        min_similarity: float,
        timeout_seconds: float = 20.0,
        safety_gate: SafetyGate | None = None,
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._generator = generator
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._timeout_seconds = timeout_seconds
        self._safety_gate = safety_gate

    def answer(self, question: str) -> AssistantAnswer:
        with answer_budget(self._timeout_seconds):
            return self._answer(question)

    def _answer(self, question: str) -> AssistantAnswer:
        with activity_step("policy"):
            local = local_safety_verdict(question)
            decision = evaluate_question(question)
        if local is SafetyVerdict.SENSITIVE_DATA:
            return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
        if local is SafetyVerdict.SELF_HARM:
            return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SUPPORT)
        if local is not SafetyVerdict.ALLOW:
            return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
        logger.info(
            "policy decision",
            extra={"outcome": decision.outcome.value, "rule": decision.matched_rule},
        )

        if decision.outcome is PolicyOutcome.PROMPT_INJECTION:
            return _refused(AnswerReason.PROMPT_INJECTION, NOTICE_INJECTION)
        if decision.outcome is PolicyOutcome.OUT_OF_SCOPE:
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
        if decision.outcome is PolicyOutcome.CAPABILITIES:
            return AssistantAnswer(
                status=AnswerStatus.ANSWERED,
                reason=AnswerReason.CAPABILITIES,
                notice=NOTICE_ROLE
                if decision.matched_rule == "assistant_role"
                else NOTICE_CAPABILITIES,
            )

        if self._safety_gate is not None:
            try:
                verdict = self._safety_gate.check(question)
            except SafetyCheckError:
                return AssistantAnswer(
                    status=AnswerStatus.FAILED,
                    reason=AnswerReason.SAFETY_CHECK_UNAVAILABLE,
                    notice=NOTICE_SAFETY_UNAVAILABLE,
                )
            if verdict is SafetyVerdict.ASSISTANT_HELP:
                return AssistantAnswer(
                    status=AnswerStatus.ANSWERED,
                    reason=AnswerReason.CAPABILITIES,
                    notice=NOTICE_CAPABILITIES,
                )
            if verdict is SafetyVerdict.INJECTION:
                return _refused(AnswerReason.PROMPT_INJECTION, NOTICE_INJECTION)
            if verdict is SafetyVerdict.SENSITIVE_DATA:
                return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
            if verdict is SafetyVerdict.SELF_HARM:
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SUPPORT)
            if verdict is SafetyVerdict.PERSONALIZED_ADVICE:
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            if verdict is SafetyVerdict.OUT_OF_SCOPE:
                if not decision.in_scope_terms:
                    return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
                # Scope is an evidence question, not a safety veto. Domain questions with
                # missing facts must reach retrieval and return unsupported, not refused.
                verdict = SafetyVerdict.ALLOW
            if verdict is not SafetyVerdict.ALLOW:
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)

        personalized = decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
        mode = GenerationMode.GENERAL_EDUCATION if personalized else GenerationMode.STANDARD

        try:
            with activity_step("embedding", self._embedder.model_id):
                vector = self._embedder.embed_query(expand_retrieval_query(question))
            with activity_step("retrieval") as search:
                passages = self._retriever.search(
                    vector, top_k=self._top_k, min_similarity=self._min_similarity
                )
                search.source_count = len(passages)
        except Exception as exc:
            logger.warning("retrieval failed", extra={"error_type": type(exc).__name__})
            return _failed()

        if not passages:
            if personalized:
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            if decision.in_scope_terms:
                return _unsupported(AnswerReason.NO_RELEVANT_EVIDENCE)
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)

        labeled = _label(passages)
        try:
            with activity_step("generation"):
                result = self._generator.generate(question, labeled, mode)
        except GenerationError as exc:
            logger.warning("generation failed", extra={"error_type": type(exc).__name__})
            if personalized:
                # The refusal itself is the essential response; education is a bonus.
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            return _failed(retrieved=len(labeled))

        with activity_step("privacy_check"):
            claims = result.draft.explanation + result.draft.key_considerations
            risks = [local_safety_verdict(c.text) for c in claims]
            if SafetyVerdict.SENSITIVE_DATA in risks:
                return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
            if any(r is not SafetyVerdict.ALLOW for r in risks):
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
        with activity_step("verification"):
            verification = verify_answer(result.draft, labeled)

        def build(
            status: AnswerStatus, reason: AnswerReason | None, notice: str | None
        ) -> AssistantAnswer:
            return AssistantAnswer(
                status=status,
                reason=reason,
                notice=notice,
                model_id=result.model_id,
                usage=result.usage,
                retrieved_passages=len(labeled),
                dropped_claims=verification.dropped_claims,
            )

        logger.info(
            "answer verified",
            extra={
                "answerable": result.draft.answerable,
                "kept_claims": len(verification.explanation) + len(verification.key_considerations),
                "dropped_claims": verification.dropped_claims,
                "rejected_citations": verification.rejected_citations,
            },
        )

        if not result.draft.answerable or not verification.explanation:
            if personalized:
                return build(
                    AnswerStatus.REFUSED, AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED
                )
            reason = (
                AnswerReason.MODEL_FOUND_INSUFFICIENT
                if not result.draft.answerable
                else AnswerReason.CITATIONS_FAILED_VERIFICATION
            )
            return build(AnswerStatus.UNSUPPORTED, reason, NOTICE_UNSUPPORTED)

        if self._safety_gate is not None:
            try:
                output_verdict = self._safety_gate.check_output(
                    " ".join(
                        c.text for c in verification.explanation + verification.key_considerations
                    )
                )
            except SafetyCheckError:
                return build(
                    AnswerStatus.FAILED,
                    AnswerReason.SAFETY_CHECK_UNAVAILABLE,
                    NOTICE_SAFETY_UNAVAILABLE,
                )
            if output_verdict is not SafetyVerdict.ALLOW:
                reason = (
                    AnswerReason.SENSITIVE_DATA
                    if output_verdict is SafetyVerdict.SENSITIVE_DATA
                    else AnswerReason.SAFETY_VIOLATION
                )
                return build(
                    AnswerStatus.REFUSED,
                    reason,
                    NOTICE_PRIVACY if reason is AnswerReason.SENSITIVE_DATA else NOTICE_SAFETY,
                )

        if personalized:
            answer = build(
                AnswerStatus.REFUSED,
                AnswerReason.PERSONALIZED_ADVICE,
                NOTICE_PERSONALIZED_WITH_EDUCATION,
            )
        else:
            answer = build(AnswerStatus.ANSWERED, None, None)
        answer.explanation = verification.explanation
        answer.key_considerations = verification.key_considerations
        answer.citations = verification.citations
        return answer


def _label(passages: list[RetrievedPassage]) -> list[LabeledPassage]:
    labeled: list[LabeledPassage] = []
    budget = MAX_CONTEXT_CHARS
    for i, p in enumerate(passages, start=1):
        if len(p.content) > budget:
            break
        budget -= len(p.content)
        labeled.append(
            LabeledPassage(
                label=f"P{i}",
                chunk_id=p.chunk_id,
                document_id=p.document_id,
                title=p.title,
                publisher=p.publisher,
                source_url=p.source_url,
                page_number=p.page_number,
                section=p.section,
                content=p.content,
                similarity=p.similarity,
            )
        )
    return labeled


def _refused(reason: AnswerReason, notice: str) -> AssistantAnswer:
    return AssistantAnswer(status=AnswerStatus.REFUSED, reason=reason, notice=notice)


def _unsupported(reason: AnswerReason) -> AssistantAnswer:
    return AssistantAnswer(
        status=AnswerStatus.UNSUPPORTED, reason=reason, notice=NOTICE_UNSUPPORTED
    )


def _failed(retrieved: int = 0) -> AssistantAnswer:
    return AssistantAnswer(
        status=AnswerStatus.FAILED,
        reason=AnswerReason.MODEL_ERROR,
        notice=NOTICE_FAILED,
        retrieved_passages=retrieved,
    )
