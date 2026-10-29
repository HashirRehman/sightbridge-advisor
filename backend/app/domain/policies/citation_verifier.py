"""Deterministic checks on the model's structured answer before anything is shown.

A claim survives only if at least one of its citations:
  1. names a passage that was actually retrieved for this question, and
  2. carries a quote that appears verbatim (after whitespace/quote normalisation) in that
     passage's stored text.
and the claim itself:
  3. shares enough content words with its cited passages to plausibly be about them, and
  4. contains no personalised-advice language.

This proves *traceability*, not truth: a real quote can still be paraphrased wrongly. That
residual risk is covered by the prompt, low temperature, the eval set and admin review.
"""

from dataclasses import dataclass
from urllib.parse import quote as url_quote

from app.domain.models.answers import (
    AnswerDraft,
    Citation,
    DraftClaim,
    LabeledPassage,
    VerifiedClaim,
)
from app.domain.policies.question_policy import contains_advice_language
from app.domain.policies.text import content_words, normalize

MIN_QUOTE_WORDS = 4
# Fraction of the claim's content words that must appear in the cited passage text.
MIN_CLAIM_OVERLAP = 0.3


@dataclass(frozen=True, slots=True)
class VerificationResult:
    explanation: list[VerifiedClaim]
    key_considerations: list[VerifiedClaim]
    citations: list[Citation]
    dropped_claims: int
    rejected_citations: int


def deep_link(passage: LabeledPassage, quote: str) -> str:
    """Link straight to the evidence: PDF page anchor, or a browser text fragment."""
    if passage.page_number is not None:
        return f"{passage.source_url}#page={passage.page_number}"
    words = quote.split()
    if len(words) >= MIN_QUOTE_WORDS:
        fragment = url_quote(" ".join(words[:8]), safe="")
        return f"{passage.source_url}#:~:text={fragment}"
    return passage.source_url


def quote_matches(quote: str, text: str) -> bool:
    """The single definition of "this quote appears in this text", tolerant of whitespace,
    quote-mark and dash variants (PDF line breaks) but otherwise exact."""
    q = normalize(quote).strip(" .")
    return len(q.split()) >= MIN_QUOTE_WORDS and q in normalize(text)


def _claim_overlaps(claim: str, passages: list[LabeledPassage]) -> bool:
    words = content_words(claim)
    if not words:
        return False
    evidence = set().union(*(content_words(p.content) for p in passages))
    return len(words & evidence) / len(words) >= MIN_CLAIM_OVERLAP


class _CitationRegistry:
    """Numbers citations by first use; one number per passage, collecting its quotes."""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}
        self._quotes: dict[str, list[str]] = {}
        self._passages: dict[str, LabeledPassage] = {}

    def add(self, passage: LabeledPassage, quote: str) -> int:
        if passage.label not in self._numbers:
            self._numbers[passage.label] = len(self._numbers) + 1
            self._quotes[passage.label] = []
            self._passages[passage.label] = passage
        if quote not in self._quotes[passage.label]:
            self._quotes[passage.label].append(quote)
        return self._numbers[passage.label]

    def citations(self) -> list[Citation]:
        out = []
        for label, number in self._numbers.items():
            p, quotes = self._passages[label], self._quotes[label]
            out.append(
                Citation(
                    number=number,
                    chunk_id=p.chunk_id,
                    document_id=p.document_id,
                    title=p.title,
                    publisher=p.publisher,
                    url=deep_link(p, quotes[0]),
                    source_url=p.source_url,
                    location=f"p. {p.page_number}" if p.page_number is not None else p.section,
                    quotes=tuple(quotes),
                )
            )
        return sorted(out, key=lambda c: c.number)


def verify_answer(draft: AnswerDraft, passages: list[LabeledPassage]) -> VerificationResult:
    by_label = {p.label: p for p in passages}
    registry = _CitationRegistry()
    dropped = rejected = 0

    def verify(claims: list[DraftClaim]) -> list[VerifiedClaim]:
        nonlocal dropped, rejected
        kept: list[VerifiedClaim] = []
        for claim in claims:
            text = claim.text.strip()
            valid: list[tuple[LabeledPassage, str]] = []
            for c in claim.citations:
                passage = by_label.get(c.passage_id.strip().upper())
                if passage is not None and quote_matches(c.quote, passage.content):
                    valid.append((passage, c.quote.strip()))
                else:
                    rejected += 1
            if (
                not text
                or not valid
                or contains_advice_language(text)
                or not _claim_overlaps(text, [p for p, _ in valid])
            ):
                dropped += 1
                continue
            numbers = tuple(dict.fromkeys(registry.add(p, q) for p, q in valid))
            kept.append(VerifiedClaim(text=text, citation_numbers=numbers))
        return kept

    explanation = verify(draft.explanation[:4])
    considerations = verify(draft.key_considerations[:5])
    return VerificationResult(
        explanation=explanation,
        key_considerations=considerations,
        citations=registry.citations(),
        dropped_claims=dropped,
        rejected_citations=rejected,
    )
