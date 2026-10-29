"""Grounded question answering: a fixed, bounded pipeline with no agent loop.

policy → embed → retrieve (similarity floor) → generate (structured) → verify citations →
answer + disclaimer. Every branch returns an AssistantAnswer with a status and disclaimer; no
exception escapes for expected failures.
"""

import logging

from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator, GenerationError, GenerationMode
from app.domain.interfaces.retrieval import PassageRetriever
from app.domain.models.answers import (
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    LabeledPassage,
)
from app.domain.models.documents import RetrievedPassage
from app.domain.policies.citation_verifier import verify_answer
from app.domain.policies.question_policy import PolicyOutcome, evaluate_question

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 9000

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
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._generator = generator
        self._top_k = top_k
        self._min_similarity = min_similarity

    def answer(self, question: str) -> AssistantAnswer:
        decision = evaluate_question(question)
        logger.info(
            "policy decision",
            extra={"outcome": decision.outcome.value, "rule": decision.matched_rule},
        )

        if decision.outcome is PolicyOutcome.PROMPT_INJECTION:
            return _refused(AnswerReason.PROMPT_INJECTION, NOTICE_INJECTION)
        if decision.outcome is PolicyOutcome.OUT_OF_SCOPE:
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)

        personalized = decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
        mode = GenerationMode.GENERAL_EDUCATION if personalized else GenerationMode.STANDARD

        try:
            passages = self._retriever.search(
                self._embedder.embed_query(question),
                top_k=self._top_k,
                min_similarity=self._min_similarity,
            )
        except Exception:
            logger.exception("retrieval failed")
            return _failed()

        if not passages:
            if personalized:
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            if decision.in_scope_terms:
                return _unsupported(AnswerReason.NO_RELEVANT_EVIDENCE)
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)

        labeled = _label(passages)
        try:
            result = self._generator.generate(question, labeled, mode)
        except GenerationError:
            logger.warning("generation failed", exc_info=True)
            if personalized:
                # The refusal itself is the essential response; education is a bonus.
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            return _failed(retrieved=len(labeled))

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
