import logging
from collections.abc import Sequence

from app.domain.interfaces.generation import (
    AnswerGenerator,
    GenerationError,
    GenerationMode,
    GenerationResult,
)
from app.domain.models.answers import LabeledPassage

logger = logging.getLogger(__name__)


class FallbackAnswerGenerator:
    """Tries each generator in order; the next one runs only if the previous raised
    GenerationError (throttling, outage, malformed output). Every result still goes through
    the same citation verification, and the audit log records which model answered."""

    def __init__(self, generators: Sequence[AnswerGenerator]) -> None:
        if not generators:
            raise ValueError("at least one generator is required")
        self._generators = list(generators)

    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult:
        last_error: GenerationError | None = None
        for index, generator in enumerate(self._generators):
            try:
                return generator.generate(question, passages, mode)
            except GenerationError as exc:
                last_error = exc
                logger.warning("generator failed", extra={"position": index, "error": str(exc)})
        assert last_error is not None
        raise last_error
