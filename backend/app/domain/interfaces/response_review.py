from dataclasses import dataclass
from typing import Protocol

from app.domain.interfaces.safety import SafetyVerdict
from app.domain.models.answers import Citation, LabeledPassage, VerifiedClaim


@dataclass(frozen=True)
class ResponseReview:
    # The proposed answer. The request's own verdict is kept apart: when the planner has
    # already screened the request, the reviewer's second opinion on it is not used.
    safety: SafetyVerdict
    # Indexes into explanation + key_considerations, in that order.
    failed_claim_indexes: tuple[int, ...] = ()
    issue: str | None = None
    request_safety: SafetyVerdict = SafetyVerdict.ALLOW


class ResponseReviewer(Protocol):
    def review(
        self,
        # None when the request was already screened by the planner.
        question: str | None,
        claims: list[VerifiedClaim],
        citations: list[Citation],
        *,
        passages: list[LabeledPassage],
    ) -> ResponseReview: ...
