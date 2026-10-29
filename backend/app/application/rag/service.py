"""Grounded conversation with a small, bounded number of model calls.

    local guard → plan → retrieve → generate → verify quotes → review → answer

A normal factual turn costs one planning call, one generation and one review, plus a query
embedding. Clear factual templates and "make it simpler" skip planning; capability questions
and source follow-ups need no model at all. Extra calls happen only after a failure: the
fallback model on an error, one search refinement when nothing is supported, one rewrite
when a simplification copies the previous answer or misquotes its evidence, and one
regeneration when the review rejects the core of the explanation. Every branch returns an
AssistantAnswer with a disclaimer.
"""

import logging
import re
from dataclasses import replace
from difflib import SequenceMatcher

from app.application.rag.activity import activity_step
from app.application.rag.budget import answer_budget, remaining_seconds
from app.application.rag.prompts import (
    OVERVIEW_NOTE,
    QUOTE_NOTE,
    REWRITE_COPY_NOTE,
    answer_body,
    repair_note,
    resolved_topic_note,
    rewrite_task,
    source_task,
)
from app.domain.interfaces.conversation import ConversationPlan, ConversationPlanner
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import (
    AnswerGenerator,
    GenerationError,
    GenerationMode,
    GenerationResult,
)
from app.domain.interfaces.response_review import ResponseReviewer
from app.domain.interfaces.retrieval import PassageRetriever
from app.domain.interfaces.safety import SafetyCheckError, SafetyVerdict
from app.domain.models.answers import (
    DISCLAIMER,
    AnswerDraft,
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    Citation,
    LabeledPassage,
    VerifiedClaim,
)
from app.domain.models.documents import RetrievedPassage
from app.domain.policies.citation_verifier import (
    VerificationResult,
    deep_link,
    quote_matches,
    verify_answer,
)
from app.domain.policies.conversation_efficiency import (
    acknowledgement,
    direct_factual_request,
    evidence_only_rewrite,
    evidence_reference_followup,
    help_reply_requires_sources,
)
from app.domain.policies.question_policy import (
    PolicyDecision,
    PolicyOutcome,
    contains_advice_language,
    evaluate_question,
)
from app.domain.policies.retrieval_query import expand_retrieval_query, related_topic_queries
from app.domain.policies.safety import local_safety_verdict

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 9000
# A simplification this similar to the previous answer did not simplify anything.
NEAR_COPY_RATIO = 0.75
# Another draft needs a generation (~10-15 s for DeepSeek) and usually a review.
RETRY_MIN_SECONDS = 12.0

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
NOTICE_ACKNOWLEDGED = (
    "You're welcome. If anything else about 1031 exchanges, DSTs or 721 exchanges is "
    "unclear, just ask."
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

_ALL_SUBJECTS = [
    "What do approved sources say about 1031 exchanges?",
    "What do approved sources say about Delaware Statutory Trusts?",
    "What do approved sources say about 721 UPREIT exchanges?",
]


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
        planner: ConversationPlanner | None = None,
        reviewer: ResponseReviewer | None = None,
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._generator = generator
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._timeout_seconds = timeout_seconds
        self._planner = planner
        self._reviewer = reviewer

    @property
    def embedding_model_id(self) -> str:
        return self._embedder.model_id

    @property
    def supports_evidence_reuse(self) -> bool:
        """Rewrites and source follow-ups reuse cited passages only under semantic review."""
        return self._reviewer is not None

    def answer(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        evidence: list[RetrievedPassage] | None = None,
        source_citations: list[Citation] | None = None,
    ) -> AssistantAnswer:
        with answer_budget(self._timeout_seconds):
            return self._answer(question, history or [], evidence or [], source_citations or [])

    def _answer(
        self,
        question: str,
        history: list[dict[str, str]],
        evidence: list[RetrievedPassage],
        source_citations: list[Citation],
    ) -> AssistantAnswer:
        with activity_step("policy"):
            local = local_safety_verdict(question)
            decision = evaluate_question(question)
        logger.info(
            "policy decision",
            extra={"outcome": decision.outcome.value, "rule": decision.matched_rule},
        )
        if (refusal := _local_refusal(local, decision)) is not None:
            return refusal

        if history and evidence and source_citations and evidence_reference_followup(question):
            with activity_step("verification"):
                provenance = _source_provenance_answer(evidence, source_citations)
            if provenance is not None:
                return provenance

        try:
            plan = self._plan(question, history, evidence, decision)
        except SafetyCheckError:
            return _safety_unavailable()
        if (refusal := _screen(plan, decision)) is not None:
            return refusal

        overview = False
        if plan.action in ("help", "clarify"):
            reply = plan.reply.replace(DISCLAIMER, "").strip()
            if not help_reply_requires_sources(reply):
                if local_safety_verdict(reply) is not SafetyVerdict.ALLOW or (
                    contains_advice_language(reply)
                ):
                    return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
                return AssistantAnswer(
                    status=AnswerStatus.ANSWERED,
                    reason=AnswerReason.CLARIFICATION
                    if plan.action == "clarify"
                    else AnswerReason.CAPABILITIES,
                    notice=reply,
                    model_id=plan.model_id,
                )
            if decision.outcome is PolicyOutcome.CAPABILITIES:
                return _capabilities(decision)
            # The reply drifted into transaction facts; answer those from the documents.
            overview = True
            plan = ConversationPlan(
                action="search",
                search_queries=related_topic_queries(question) or _ALL_SUBJECTS,
            )

        personalized = decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
        try:
            passages = (
                list(evidence) if plan.action in ("rewrite", "cite") else self._retrieve(plan)
            )
            refined = not passages and self._refinable(plan)
            if refined:
                passages = self._refine(plan)
        except Exception as exc:
            logger.warning("retrieval failed", extra={"error_type": type(exc).__name__})
            return _failed()
        if not passages:
            if personalized:
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            if decision.in_scope_terms or evaluate_question(plan.query).in_scope_terms:
                return _unsupported(AnswerReason.NO_RELEVANT_EVIDENCE)
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)

        return self._grounded_answer(
            question,
            self._task(question, plan, history, overview),
            plan,
            passages,
            history,
            personalized=personalized,
            refined=refined,
        )

    def _plan(
        self,
        question: str,
        history: list[dict[str, str]],
        evidence: list[RetrievedPassage],
        decision: PolicyDecision,
    ) -> ConversationPlan:
        """Deterministic plans when the request is unambiguous; one model call otherwise."""
        if decision.outcome is PolicyOutcome.CAPABILITIES and (
            self._planner is None or not _answered_capabilities(history)
        ):
            return ConversationPlan(action="help", reply=_capabilities(decision).notice or "")
        if acknowledgement(question):
            return ConversationPlan(action="help", reply=NOTICE_ACKNOWLEDGED)
        if evidence and history and evidence_only_rewrite(question):
            return ConversationPlan(action="rewrite")
        if evidence and history and evidence_reference_followup(question):
            return ConversationPlan(action="cite")
        if self._planner is None or direct_factual_request(question):
            return ConversationPlan(action="search", search_queries=[question])
        return self._planner.plan(question, history)

    def _task(
        self,
        question: str,
        plan: ConversationPlan,
        history: list[dict[str, str]],
        overview: bool,
    ) -> str:
        """The generator always sees the original request; notes only add context."""
        if plan.action == "rewrite":
            return rewrite_task(question, history[-1]["answer"])
        if plan.action == "cite":
            return source_task(question, history[-1]["answer"])
        task = question
        if plan.query and plan.query != question:
            task += resolved_topic_note(plan.query)
        return task + OVERVIEW_NOTE if overview else task

    def _retrieve(self, plan: ConversationPlan) -> list[RetrievedPassage]:
        queries = plan.search_queries
        per_query = max(1, self._top_k // len(queries))
        passages: list[RetrievedPassage] = []
        for index, query in enumerate(queries):
            for rank, passage in enumerate(self._search(query, per_query)):
                if any(passage.chunk_id == p.chunk_id for p in passages):
                    continue
                if rank == 0:
                    # Keep one strong passage per subject before spending the context
                    # budget on additional passages about a single subject.
                    passages.insert(min(index, len(passages)), passage)
                else:
                    passages.append(passage)
        return passages[: self._top_k]

    def _search(self, query: str, top_k: int) -> list[RetrievedPassage]:
        with activity_step("embedding", self._embedder.model_id):
            vector = self._embedder.embed_query(expand_retrieval_query(query))
        with activity_step("retrieval") as step:
            found = self._retriever.search(vector, top_k=top_k, min_similarity=self._min_similarity)
            step.source_count = len(found)
        return found

    def _refinable(self, plan: ConversationPlan) -> bool:
        alternative = plan.alternative_query.strip().casefold()
        return (
            plan.action == "search"
            and bool(alternative)
            and alternative != (plan.query.strip().casefold())
        )

    def _refine(self, plan: ConversationPlan) -> list[RetrievedPassage]:
        with activity_step("search_refinement"):
            return self._search(plan.alternative_query, self._top_k)

    def _grounded_answer(
        self,
        question: str,
        task: str,
        plan: ConversationPlan,
        passages: list[RetrievedPassage],
        history: list[dict[str, str]],
        *,
        personalized: bool,
        refined: bool,
    ) -> AssistantAnswer:
        mode = GenerationMode.GENERAL_EDUCATION if personalized else GenerationMode.STANDARD
        for attempt in range(2):
            labeled = _label(passages)
            try:
                with activity_step("generation"):
                    result = self._generator.generate(task, labeled, mode)
            except GenerationError as exc:
                logger.warning("generation failed", extra={"error_type": type(exc).__name__})
                if personalized:
                    # The refusal itself is the essential response; education is a bonus.
                    return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
                return _failed(retrieved=len(labeled))

            with activity_step("privacy_check"):
                claims = result.draft.explanation + result.draft.key_considerations
                risks = {local_safety_verdict(c.text) for c in claims}
            if SafetyVerdict.SENSITIVE_DATA in risks:
                return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
            if risks - {SafetyVerdict.ALLOW}:
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
            with activity_step("verification"):
                verification = verify_answer(result.draft, labeled)

            # A retry that can't finish inside the budget only adds billed work before an
            # error, so the best checked result so far is final once time runs short.
            final = attempt == 1 or remaining_seconds(RETRY_MIN_SECONDS) < RETRY_MIN_SECONDS
            if not (result.draft.answerable and verification.explanation):
                if final:
                    break
                if plan.action in ("rewrite", "cite") and result.draft.answerable:
                    # Reused evidence has no search to refine; a draft whose quotes all
                    # failed verification gets one regeneration instead.
                    task += QUOTE_NOTE
                    continue
                if refined or not self._refinable(plan):
                    break
                # Nothing supported in the first passages: one bounded search refinement.
                refined = True
                try:
                    replacement = self._refine(plan)
                except Exception as exc:
                    logger.warning(
                        "search refinement failed", extra={"error_type": type(exc).__name__}
                    )
                    break
                if not replacement:
                    break
                passages = replacement
                continue
            if (
                not final
                and plan.action == "rewrite"
                and _near_copy(verification, history[-1]["answer"])
            ):
                task += REWRITE_COPY_NOTE
                continue
            if self._reviewer is None:
                break
            try:
                review = self._reviewer.review(
                    question,
                    verification.explanation + verification.key_considerations,
                    verification.citations,
                    passages=labeled,
                )
            except GenerationError:
                return _failed(retrieved=len(labeled))
            if review.safety is not SafetyVerdict.ALLOW:
                return _safety_refusal(review.safety)
            if not review.failed_claim_indexes:
                break
            rejected = _claim_texts(verification, review.failed_claim_indexes)
            trimmed = _without_claims(result.draft, verification, rejected, labeled)
            removed = len(verification.explanation) - len(trimmed.explanation)
            verification = trimmed
            if trimmed.explanation and (
                final or removed == 0 or (removed == 1 and len(trimmed.explanation) >= 2)
            ):
                # The direct answer is still intact: publish the supported claims instead
                # of paying for a regeneration and a second review. A gutted explanation
                # reads as fragments, so that case gets the one repair attempt below.
                break
            if final:
                return _built(
                    result,
                    labeled,
                    verification,
                    AnswerStatus.UNSUPPORTED,
                    AnswerReason.CITATIONS_FAILED_VERIFICATION,
                    NOTICE_UNSUPPORTED,
                )
            task += repair_note(review.issue or "unsupported_inference", sorted(rejected))

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
                return _built(
                    result,
                    labeled,
                    verification,
                    AnswerStatus.REFUSED,
                    AnswerReason.PERSONALIZED_ADVICE,
                    NOTICE_PERSONALIZED,
                )
            return _built(
                result,
                labeled,
                verification,
                AnswerStatus.UNSUPPORTED,
                AnswerReason.MODEL_FOUND_INSUFFICIENT
                if not result.draft.answerable
                else AnswerReason.CITATIONS_FAILED_VERIFICATION,
                NOTICE_UNSUPPORTED,
            )
        answer = _built(
            result,
            labeled,
            verification,
            AnswerStatus.REFUSED if personalized else AnswerStatus.ANSWERED,
            AnswerReason.PERSONALIZED_ADVICE if personalized else None,
            NOTICE_PERSONALIZED_WITH_EDUCATION if personalized else None,
        )
        answer.explanation = verification.explanation
        answer.key_considerations = verification.key_considerations
        answer.citations = verification.citations
        return answer


def _local_refusal(local: SafetyVerdict, decision: PolicyDecision) -> AssistantAnswer | None:
    """High-confidence rules that never need a model."""
    if local is SafetyVerdict.SENSITIVE_DATA:
        return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
    if local is SafetyVerdict.SELF_HARM:
        return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SUPPORT)
    if local is not SafetyVerdict.ALLOW:
        return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
    if decision.outcome is PolicyOutcome.PROMPT_INJECTION:
        return _refused(AnswerReason.PROMPT_INJECTION, NOTICE_INJECTION)
    if decision.outcome is PolicyOutcome.OUT_OF_SCOPE:
        return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
    return None


def _screen(plan: ConversationPlan, decision: PolicyDecision) -> AssistantAnswer | None:
    """Apply the planner's semantic verdict; it can add refusals but never remove local ones."""
    verdict = plan.safety_verdict
    if verdict is SafetyVerdict.OUT_OF_SCOPE:
        # Scope is an evidence question, not a safety veto: domain questions reach retrieval
        # and return unsupported when nothing matches. App help is always in scope.
        in_scope = decision.in_scope_terms or evaluate_question(plan.query).in_scope_terms
        if plan.action == "search" and not in_scope:
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
    elif verdict not in (SafetyVerdict.ALLOW, SafetyVerdict.ASSISTANT_HELP):
        return _safety_refusal(verdict)
    if decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE and plan.action != "search":
        return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
    return None


def _capabilities(decision: PolicyDecision) -> AssistantAnswer:
    return AssistantAnswer(
        status=AnswerStatus.ANSWERED,
        reason=AnswerReason.CAPABILITIES,
        notice=NOTICE_ROLE if decision.matched_rule == "assistant_role" else NOTICE_CAPABILITIES,
    )


def _answered_capabilities(history: list[dict[str, str]]) -> bool:
    return any(
        NOTICE_CAPABILITIES in turn["answer"] or NOTICE_ROLE in turn["answer"] for turn in history
    )


def _near_copy(verification: VerificationResult, previous_answer: str) -> bool:
    previous = re.sub(r"\s*\[\d+\]", "", answer_body(previous_answer)).casefold()
    draft = " ".join(c.text for c in verification.explanation).casefold()
    return all(c.text.casefold() in previous for c in verification.explanation) or (
        SequenceMatcher(None, draft, previous).ratio() >= NEAR_COPY_RATIO
    )


def _claim_texts(verification: VerificationResult, indexes: tuple[int, ...]) -> set[str]:
    claims = verification.explanation + verification.key_considerations
    return {claims[i].text for i in indexes if 0 <= i < len(claims)}


def _without_claims(
    draft: AnswerDraft,
    verification: VerificationResult,
    rejected: set[str],
    passages: list[LabeledPassage],
) -> VerificationResult:
    """Re-verify without rejected claims so citation numbers and quotes stay exact."""
    kept = draft.model_copy(
        update={
            "explanation": [c for c in draft.explanation if c.text.strip() not in rejected],
            "key_considerations": [
                c for c in draft.key_considerations if c.text.strip() not in rejected
            ],
        }
    )
    result = verify_answer(kept, passages)
    return replace(result, dropped_claims=verification.dropped_claims + len(rejected))


def _built(
    result: GenerationResult,
    labeled: list[LabeledPassage],
    verification: VerificationResult,
    status: AnswerStatus,
    reason: AnswerReason | None,
    notice: str | None,
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


def _label(passages: list[RetrievedPassage]) -> list[LabeledPassage]:
    labeled: list[LabeledPassage] = []
    budget = MAX_CONTEXT_CHARS
    for i, p in enumerate(passages, start=1):
        if len(p.content) > budget:
            break
        budget -= len(p.content)
        labeled.append(_labeled(p, f"P{i}"))
    return labeled


def _labeled(p: RetrievedPassage, label: str) -> LabeledPassage:
    return LabeledPassage(
        label=label,
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


def _source_provenance_answer(
    passages: list[RetrievedPassage], saved: list[Citation]
) -> AssistantAnswer | None:
    """Answer a source follow-up from current, previously cited documents."""
    by_id = {p.chunk_id: p for p in passages}
    citations: list[Citation] = []
    names: list[str] = []
    for prior in saved:
        passage = by_id.get(prior.chunk_id)
        if passage is None or passage.document_id != prior.document_id or not prior.quotes:
            return None
        if not all(quote_matches(quote, passage.content) for quote in prior.quotes):
            return None
        number = len(citations) + 1
        citations.append(
            Citation(
                number=number,
                chunk_id=passage.chunk_id,
                document_id=passage.document_id,
                title=passage.title,
                publisher=passage.publisher,
                url=deep_link(_labeled(passage, f"P{number}"), prior.quotes[0]),
                source_url=passage.source_url,
                location=passage.location,
                quotes=prior.quotes,
            )
        )
        name = f"{passage.title} ({passage.publisher})"
        if name not in names:
            names.append(name)
    if not citations:
        return None
    return AssistantAnswer(
        status=AnswerStatus.ANSWERED,
        explanation=[
            VerifiedClaim(
                "The previous answer drew on " + "; ".join(names) + ".",
                tuple(c.number for c in citations),
            )
        ],
        citations=citations,
        retrieved_passages=len(passages),
    )


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


def _safety_unavailable() -> AssistantAnswer:
    return AssistantAnswer(
        status=AnswerStatus.FAILED,
        reason=AnswerReason.SAFETY_CHECK_UNAVAILABLE,
        notice=NOTICE_SAFETY_UNAVAILABLE,
    )


def _safety_refusal(verdict: SafetyVerdict) -> AssistantAnswer:
    if verdict is SafetyVerdict.SENSITIVE_DATA:
        return _refused(AnswerReason.SENSITIVE_DATA, NOTICE_PRIVACY)
    if verdict is SafetyVerdict.SELF_HARM:
        return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SUPPORT)
    if verdict is SafetyVerdict.PERSONALIZED_ADVICE:
        return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
    if verdict is SafetyVerdict.INJECTION:
        return _refused(AnswerReason.PROMPT_INJECTION, NOTICE_INJECTION)
    if verdict is SafetyVerdict.OUT_OF_SCOPE:
        return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
    return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
