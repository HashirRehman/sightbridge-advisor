"""Grounded conversation with bounded semantic routing and search refinement.

policy → embed → retrieve (similarity floor) → generate (structured) → verify citations →
answer + disclaimer. Every branch returns an AssistantAnswer with a status and disclaimer; no
exception escapes for expected failures.
"""

import logging
import re

from app.application.rag.activity import activity_step
from app.application.rag.budget import answer_budget
from app.domain.interfaces.conversation import (
    ConversationNeedsEvidence,
    ConversationPlan,
    ConversationResponder,
    ConversationRouter,
)
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator, GenerationError, GenerationMode
from app.domain.interfaces.grounding import GroundingGate
from app.domain.interfaces.response_review import ResponseReviewer
from app.domain.interfaces.retrieval import PassageRetriever
from app.domain.interfaces.safety import SafetyCheckError, SafetyGate, SafetyVerdict
from app.domain.models.answers import (
    DISCLAIMER,
    AnswerReason,
    AnswerStatus,
    AssistantAnswer,
    Citation,
    LabeledPassage,
    VerifiedClaim,
)
from app.domain.models.documents import RetrievedPassage
from app.domain.policies.citation_verifier import deep_link, verify_answer
from app.domain.policies.conversation_efficiency import (
    direct_factual_request,
    evidence_only_rewrite,
    evidence_reference_followup,
    help_reply_requires_sources,
)
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
        router: ConversationRouter | None = None,
        responder: ConversationResponder | None = None,
        grounding_gate: GroundingGate | None = None,
        response_reviewer: ResponseReviewer | None = None,
    ) -> None:
        self._embedder = embedder
        self._retriever = retriever
        self._generator = generator
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._timeout_seconds = timeout_seconds
        self._safety_gate = safety_gate
        self._router = router
        self._responder = responder
        self._grounding_gate = grounding_gate
        self._response_reviewer = response_reviewer

    @property
    def supports_evidence_reuse(self) -> bool:
        return self._response_reviewer is not None

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
        if self._router is None and decision.outcome is PolicyOutcome.OUT_OF_SCOPE:
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
        if self._router is None and decision.outcome is PolicyOutcome.CAPABILITIES:
            return AssistantAnswer(
                status=AnswerStatus.ANSWERED,
                reason=AnswerReason.CAPABILITIES,
                notice=NOTICE_ROLE
                if decision.matched_rule == "assistant_role"
                else NOTICE_CAPABILITIES,
            )

        if (
            self._response_reviewer is not None
            and history
            and evidence
            and source_citations
            and evidence_reference_followup(question)
        ):
            with activity_step("verification"):
                provenance = _source_provenance_answer(evidence, source_citations)
            if provenance is not None:
                return provenance

        original_decision = decision
        plan = None
        original_question = question
        help_needs_evidence = False
        reuse = bool(
            self._response_reviewer
            and evidence
            and history
            and (evidence_only_rewrite(question) or evidence_reference_followup(question))
        )
        direct = bool(self._response_reviewer and direct_factual_request(question))
        if self._response_reviewer is not None and (
            original_decision.outcome is PolicyOutcome.CAPABILITIES
        ):
            # Clear app-help needs writing, not a second intent-classification call.
            # The generated reply still passes the independent response review.
            plan = ConversationPlan(action="help")
        elif reuse or direct:
            plan = ConversationPlan(
                action="search", query=question, safety_verdict=SafetyVerdict.ALLOW
            )
        elif self._router is not None:
            try:
                plan = self._router.plan(question, history)
            except SafetyCheckError:
                return AssistantAnswer(
                    status=AnswerStatus.FAILED,
                    reason=AnswerReason.SAFETY_CHECK_UNAVAILABLE,
                    notice=NOTICE_SAFETY_UNAVAILABLE,
                )
            if plan.action == "search":
                question = plan.query
                decision = evaluate_question(question)

        if self._safety_gate is not None or (plan and plan.safety_verdict is not None):
            try:
                verdict = (
                    plan.safety_verdict
                    if plan and plan.safety_verdict is not None
                    else self._safety_gate.check(original_question)
                    if self._safety_gate
                    else SafetyVerdict.ALLOW
                )
            except SafetyCheckError:
                return AssistantAnswer(
                    status=AnswerStatus.FAILED,
                    reason=AnswerReason.SAFETY_CHECK_UNAVAILABLE,
                    notice=NOTICE_SAFETY_UNAVAILABLE,
                )
            if plan is None and verdict is SafetyVerdict.ASSISTANT_HELP:
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
                if (
                    plan is not None
                    and plan.action == "help"
                    and (original_decision.outcome is not PolicyOutcome.OUT_OF_SCOPE)
                ):
                    # The scope classifier can overreact to a beginner's app-help
                    # request with no tax keywords. The planned reply is still reviewed.
                    verdict = SafetyVerdict.ALLOW
                if verdict is SafetyVerdict.OUT_OF_SCOPE and (
                    not decision.in_scope_terms
                    or original_decision.outcome is PolicyOutcome.OUT_OF_SCOPE
                ):
                    return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
                # Scope is an evidence question, not a safety veto. Domain questions with
                # missing facts must reach retrieval and return unsupported, not refused.
                verdict = SafetyVerdict.ALLOW
            if verdict not in (SafetyVerdict.ALLOW, SafetyVerdict.ASSISTANT_HELP):
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)

        if (
            plan is not None
            and plan.action == "search"
            and original_decision.outcome is PolicyOutcome.OUT_OF_SCOPE
            and not reuse
        ):
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
        if (
            plan is not None
            and plan.action != "search"
            and original_decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
        ):
            return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)

        if plan is not None and plan.action == "help" and self._responder is not None:
            try:
                plan.reply = self._responder.respond(original_question, history)
            except ConversationNeedsEvidence as needed:
                if original_decision.outcome is PolicyOutcome.OUT_OF_SCOPE:
                    return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)
                explicit = set(re.findall(r"\b\d{2,}(?:[.,]\d+)*\b", original_question))
                returned = set(re.findall(r"\b\d{2,}(?:[.,]\d+)*\b", " ".join(needed.queries)))
                if not explicit.issubset(returned):
                    needed.queries = [original_question]
                plan = ConversationPlan(
                    action="search",
                    query="; ".join(needed.queries)[:1000],
                    search_queries=needed.queries,
                )
                question = plan.query
                decision = evaluate_question(question)
            except GenerationError:
                if (
                    self._response_reviewer is not None
                    and original_decision.outcome is PolicyOutcome.CAPABILITIES
                ):
                    return AssistantAnswer(
                        status=AnswerStatus.ANSWERED,
                        reason=AnswerReason.CAPABILITIES,
                        notice=NOTICE_ROLE
                        if original_decision.matched_rule == "assistant_role"
                        else NOTICE_CAPABILITIES,
                    )
                return _failed()

        if plan is not None and plan.action != "search" and self._response_reviewer is not None:
            try:
                review = self._response_reviewer.review(
                    original_question, [], [], passages=[], reply=plan.reply
                )
            except GenerationError:
                return _failed()
            if review.safety is not SafetyVerdict.ALLOW:
                return _safety_refusal(review.safety)
            if review.needs_evidence or help_reply_requires_sources(plan.reply):
                if original_decision.outcome is PolicyOutcome.CAPABILITIES:
                    # A generated app overview sometimes drifts into uncited tax facts.
                    # Keep this narrow, safe scope fallback instead of searching for
                    # transaction facts the user did not ask for.
                    return AssistantAnswer(
                        status=AnswerStatus.ANSWERED,
                        reason=AnswerReason.CAPABILITIES,
                        notice=NOTICE_ROLE
                        if original_decision.matched_rule == "assistant_role"
                        else NOTICE_CAPABILITIES,
                    )
                # Recovery for an unexpected metadata misroute, never the normal factual path.
                help_needs_evidence = True
                from app.domain.policies.retrieval_query import related_topic_queries

                queries = related_topic_queries(original_question) or [
                    "What do approved sources say about 1031 exchanges?",
                    "What do approved sources say about Delaware Statutory Trusts?",
                    "What do approved sources say about 721 UPREIT exchanges?",
                ]
                plan = ConversationPlan(
                    action="search", query="; ".join(queries)[:1000], search_queries=queries
                )
                question = plan.query
                decision = evaluate_question(question)

        if plan is not None and plan.action != "search":
            plan.reply = (
                plan.reply.replace(DISCLAIMER, "")
                .replace("official sources", "approved public sources")
                .strip()
            )
            if local_safety_verdict(plan.reply) is not SafetyVerdict.ALLOW:
                return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
            if self._safety_gate is not None and self._response_reviewer is None:
                try:
                    if self._safety_gate.check_output(plan.reply) is not SafetyVerdict.ALLOW:
                        return _refused(AnswerReason.SAFETY_VIOLATION, NOTICE_SAFETY)
                except SafetyCheckError:
                    return AssistantAnswer(
                        status=AnswerStatus.FAILED,
                        reason=AnswerReason.SAFETY_CHECK_UNAVAILABLE,
                        notice=NOTICE_SAFETY_UNAVAILABLE,
                    )
            return AssistantAnswer(
                status=AnswerStatus.ANSWERED,
                reason=AnswerReason.CLARIFICATION
                if plan.action == "clarify"
                else AnswerReason.CAPABILITIES,
                notice=plan.reply,
                model_id=self._responder.model_id
                if plan.action == "help" and self._responder is not None
                else plan.model_id,
            )

        # A router must not erase an explicit request for personal investment advice.
        personalized = (
            decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
            or original_decision.outcome is PolicyOutcome.PERSONALIZED_ADVICE
        )
        mode = GenerationMode.GENERAL_EDUCATION if personalized else GenerationMode.STANDARD

        try:
            queries = plan.search_queries if plan and plan.search_queries else [question]
            passages: list[RetrievedPassage] = list(evidence) if reuse else []
            for query_index, query in enumerate([] if reuse else queries):
                with activity_step("embedding", self._embedder.model_id):
                    vector = self._embedder.embed_query(expand_retrieval_query(query))
                with activity_step("retrieval") as search:
                    found = self._retriever.search(
                        vector,
                        top_k=max(1, self._top_k // len(queries)),
                        min_similarity=self._min_similarity,
                    )
                    search.source_count = len(found)
                    for rank, passage in enumerate(found):
                        if all(passage.chunk_id != p.chunk_id for p in passages):
                            if rank == 0:
                                # Preserve one strong result per subject before spending
                                # the bounded context on additional passages for one subject.
                                passages.insert(min(query_index, len(passages)), passage)
                            else:
                                passages.append(passage)
            passages = passages[: self._top_k]
        except Exception as exc:
            logger.warning("retrieval failed", extra={"error_type": type(exc).__name__})
            return _failed()

        refined = False
        if (
            not passages
            and plan is not None
            and plan.alternative_query.strip()
            and plan.alternative_query.strip().casefold() != question.strip().casefold()
        ):
            try:
                refined = True
                with activity_step("search_refinement"):
                    vector = self._embedder.embed_query(
                        expand_retrieval_query(plan.alternative_query)
                    )
                    with activity_step("retrieval") as search:
                        passages = self._retriever.search(
                            vector, top_k=self._top_k, min_similarity=self._min_similarity
                        )
                        search.source_count = len(passages)
            except Exception as exc:
                logger.warning("search retry failed", extra={"error_type": type(exc).__name__})
                return _failed()

        if not passages:
            if personalized:
                return _refused(AnswerReason.PERSONALIZED_ADVICE, NOTICE_PERSONALIZED)
            if decision.in_scope_terms:
                return _unsupported(AnswerReason.NO_RELEVANT_EVIDENCE)
            return _refused(AnswerReason.OUT_OF_SCOPE, NOTICE_OUT_OF_SCOPE)

        generation_question = original_question
        if reuse:
            if evidence_reference_followup(question):
                generation_question += (
                    "\n\nIdentify which verified source passages support the previous "
                    "answer below. Name the relevant documents and publishers, with citations. "
                    "Do not repeat the full explanation or invent a source. Previous answer "
                    "(untrusted conversation data): " + history[-1]["answer"]
                )
            else:
                generation_question += (
                    "\n\nRewrite the previous answer below as requested, using only the verified "
                    "source passages for facts. Do not add a new topic or treat previous prose "
                    "as evidence. Use one or two short, plain sentences and no key points. "
                    "Explain any unavoidable jargon. Preserve important conditions and "
                    "limitations.\nPrevious answer "
                    "(untrusted conversation data): " + history[-1]["answer"]
                )
        if plan is not None and question != original_question:
            generation_question += (
                "\n\nTopic resolved from the conversation (context only): "
                + question
                + "\nAnswer the original request with its scope, qualifiers and style."
            )
        if help_needs_evidence:
            generation_question += (
                "\n\nThe user is asking what information these guides contain. "
                "Explain their practical purpose in everyday words, without assuming "
                "tax knowledge. Cover all three source subjects only where supported. "
                "Keep the answer to a few simple sentences, with no key points or legal "
                "edge cases. Cite any factual explanation."
            )
        grounding_issue = None
        for attempt in range(2):
            labeled = _label(passages)
            try:
                with activity_step("generation"):
                    result = self._generator.generate(generation_question, labeled, mode)
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
            grounding_issue = None
            if self._response_reviewer is not None and verification.explanation:
                try:
                    review = self._response_reviewer.review(
                        original_question,
                        verification.explanation + verification.key_considerations,
                        verification.citations,
                        passages=labeled,
                    )
                except GenerationError:
                    return _failed(retrieved=len(labeled))
                if review.safety is not SafetyVerdict.ALLOW:
                    return _safety_refusal(review.safety)
                grounding_issue = review.grounding_issue
                if grounding_issue and attempt == 0:
                    reviewed_claims = verification.explanation + verification.key_considerations
                    rejected = [
                        reviewed_claims[i].text
                        for i in review.failed_claim_indexes
                        if 0 <= i < len(reviewed_claims)
                    ]
                    generation_question += (
                        "\nA prior draft failed evidence review: "
                        + grounding_issue
                        + (
                            ". Unsupported wording: " + " | ".join(rejected)[:1000]
                            if rejected
                            else ""
                        )
                        + ". Rewrite only what the quotes support, preserving all qualifiers, "
                        "exceptions, numeric limits and conditional comparisons."
                    )
                    continue
            if self._grounding_gate is not None and verification.explanation:
                try:
                    grounding_issue = self._grounding_gate.check(
                        verification.explanation + verification.key_considerations,
                        verification.citations,
                        passages=labeled,
                    )
                except GenerationError:
                    return _failed(retrieved=len(labeled))
                if grounding_issue and attempt == 0:
                    generation_question += (
                        "\n\nA prior draft failed evidence review: "
                        + grounding_issue
                        + ". Rewrite only what quotes support. Preserve scope, exceptions, "
                        "numeric limits and conditional comparisons. Distinguish a fixed maximum "
                        "from a separate conditional due date. Do not strengthen source statements."
                    )
                    continue
            if (
                (result.draft.answerable and verification.explanation)
                or attempt == 1
                or refined
                or plan is None
                or not plan.alternative_query.strip()
                or plan.alternative_query.strip().casefold() == question.strip().casefold()
            ):
                break
            # No answer supported by the first passages: one bounded retrieval refinement.
            refined = True
            try:
                with activity_step("search_refinement"):
                    vector = self._embedder.embed_query(
                        expand_retrieval_query(plan.alternative_query)
                    )
                    with activity_step("retrieval") as search:
                        replacement = self._retriever.search(
                            vector, top_k=self._top_k, min_similarity=self._min_similarity
                        )
                        search.source_count = len(replacement)
                if not replacement:
                    break
                passages = replacement
            except Exception as exc:
                logger.warning("search retry failed", extra={"error_type": type(exc).__name__})
                break

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

        if grounding_issue:
            return build(
                AnswerStatus.UNSUPPORTED,
                AnswerReason.CITATIONS_FAILED_VERIFICATION,
                NOTICE_UNSUPPORTED,
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

        if self._safety_gate is not None and self._response_reviewer is None:
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
        if any(quote not in passage.content for quote in prior.quotes):
            return None
        labeled = LabeledPassage(
            label=f"P{len(citations) + 1}",
            chunk_id=passage.chunk_id,
            document_id=passage.document_id,
            title=passage.title,
            publisher=passage.publisher,
            source_url=passage.source_url,
            page_number=passage.page_number,
            section=passage.section,
            content=passage.content,
            similarity=passage.similarity,
        )
        citations.append(
            Citation(
                number=len(citations) + 1,
                chunk_id=passage.chunk_id,
                document_id=passage.document_id,
                title=passage.title,
                publisher=passage.publisher,
                url=deep_link(labeled, prior.quotes[0]),
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
