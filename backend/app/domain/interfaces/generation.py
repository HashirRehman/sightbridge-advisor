from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.models.answers import AnswerDraft, LabeledPassage, TokenUsage


class GenerationMode(StrEnum):
    STANDARD = "standard"
    # The user asked for personalised advice: explain only the general concepts involved.
    GENERAL_EDUCATION = "general_education"


class GenerationError(Exception):
    """The model could not produce a usable answer (timeout, throttling, malformed output)."""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    draft: AnswerDraft
    model_id: str
    usage: TokenUsage | None


class AnswerGenerator(Protocol):
    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult: ...
