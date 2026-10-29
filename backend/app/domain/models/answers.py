from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

DISCLAIMER = (
    "Educational information only, not tax, legal or investment advice. "
    "Consult qualified tax, legal and financial professionals about any specific situation."
)


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"  # policy declined: personalised advice, out of scope, injection
    UNSUPPORTED = "unsupported"  # in scope, but the corpus does not support an answer
    FAILED = "failed"  # model/infrastructure error


class AnswerReason(StrEnum):
    PERSONALIZED_ADVICE = "personalized_advice"
    OUT_OF_SCOPE = "out_of_scope"
    PROMPT_INJECTION = "prompt_injection"
    NO_RELEVANT_EVIDENCE = "no_relevant_evidence"
    MODEL_FOUND_INSUFFICIENT = "model_found_insufficient"
    CITATIONS_FAILED_VERIFICATION = "citations_failed_verification"
    MODEL_ERROR = "model_error"


# --- Structured output requested from the model -------------------------------------------


class DraftCitation(BaseModel):
    passage_id: str = Field(description="ID of the passage relied on, e.g. 'P2'.")
    quote: str = Field(
        description="A short verbatim excerpt (one sentence or clause) copied exactly from "
        "that passage which supports the claim."
    )


class DraftClaim(BaseModel):
    text: str = Field(description="One plain-language sentence.")
    citations: list[DraftCitation] = Field(
        description="At least one supporting passage with a verbatim quote."
    )


class AnswerDraft(BaseModel):
    """Answer grounded in the provided passages."""

    answerable: bool = Field(
        description="False if the passages do not contain enough information to answer."
    )
    explanation: list[DraftClaim] = Field(
        default_factory=list,
        description="1-4 sentences that directly answer the question. Empty if not answerable.",
    )
    key_considerations: list[DraftClaim] = Field(
        default_factory=list,
        description="0-5 short points an advisor should keep in mind, only if supported.",
    )


# --- Verified answer returned to callers ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class LabeledPassage:
    """A retrieved passage with the short ID the model uses to cite it."""

    label: str
    chunk_id: UUID
    document_id: str
    title: str
    publisher: str
    source_url: str
    page_number: int | None
    section: str | None
    content: str
    similarity: float


@dataclass(frozen=True, slots=True)
class Citation:
    number: int
    chunk_id: UUID
    document_id: str
    title: str
    publisher: str
    url: str  # deep link: PDF page anchor or text fragment
    source_url: str
    location: str | None
    quotes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedClaim:
    text: str
    citation_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(slots=True)
class AssistantAnswer:
    status: AnswerStatus
    reason: AnswerReason | None = None
    notice: str | None = None  # fixed, code-authored text for refusals/fallbacks
    explanation: list[VerifiedClaim] = field(default_factory=list)
    key_considerations: list[VerifiedClaim] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    model_id: str | None = None
    usage: TokenUsage | None = None
    retrieved_passages: int = 0
    dropped_claims: int = 0
    # Always present; set by code, never by the model.
    disclaimer: str = DISCLAIMER

    def render_text(self) -> str:
        """Canonical plain-text form for the audit log (includes the disclaimer)."""
        parts: list[str] = []
        if self.notice:
            parts.append(self.notice)

        def claim(c: VerifiedClaim) -> str:
            refs = "".join(f"[{n}]" for n in c.citation_numbers)
            return f"{c.text} {refs}".strip()

        if self.explanation:
            parts.append(" ".join(claim(c) for c in self.explanation))
        if self.key_considerations:
            parts.append(
                "Key considerations:\n"
                + "\n".join(f"- {claim(c)}" for c in self.key_considerations)
            )
        if self.citations:
            parts.append(
                "Sources:\n"
                + "\n".join(
                    f"[{c.number}] {c.title} ({c.publisher}"
                    + (f", {c.location}" if c.location else "")
                    + f") {c.source_url}"
                    for c in self.citations
                )
            )
        parts.append(self.disclaimer)
        return "\n\n".join(parts)
