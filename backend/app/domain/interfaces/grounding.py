from typing import Protocol

from app.domain.models.answers import Citation, LabeledPassage, VerifiedClaim


class GroundingGate(Protocol):
    def check(
        self,
        claims: list[VerifiedClaim],
        citations: list[Citation],
        *,
        passages: list[LabeledPassage] | None = None,
    ) -> str | None: ...
