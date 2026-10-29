from dataclasses import dataclass
from typing import Protocol

from app.domain.interfaces.safety import SafetyVerdict
from app.domain.models.answers import Citation, LabeledPassage, VerifiedClaim


@dataclass(frozen=True)
class ResponseReview:
    safety: SafetyVerdict
    grounding_issue: str | None = None
    needs_evidence: bool = False
    failed_claim_indexes: tuple[int, ...] = ()


class ResponseReviewer(Protocol):
    def review(
        self,
        question: str,
        claims: list[VerifiedClaim],
        citations: list[Citation],
        *,
        passages: list[LabeledPassage],
        reply: str = "",
    ) -> ResponseReview: ...
