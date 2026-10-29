from dataclasses import dataclass
from typing import Protocol

from app.domain.interfaces.safety import SafetyVerdict
from app.domain.models.answers import Citation, LabeledPassage, VerifiedClaim


@dataclass(frozen=True)
class ResponseReview:
    safety: SafetyVerdict
    # Indexes into explanation + key_considerations, in that order.
    failed_claim_indexes: tuple[int, ...] = ()
    issue: str | None = None


class ResponseReviewer(Protocol):
    def review(
        self,
        question: str,
        claims: list[VerifiedClaim],
        citations: list[Citation],
        *,
        passages: list[LabeledPassage],
    ) -> ResponseReview: ...
