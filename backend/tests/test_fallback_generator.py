import pytest

from app.application.rag.fallback import FallbackAnswerGenerator
from app.domain.interfaces.generation import GenerationError, GenerationMode, GenerationResult
from app.domain.models.answers import LabeledPassage
from tests.factories import draft


class Scripted:
    def __init__(self, model_id: str, fail: bool) -> None:
        self.model_id, self.fail, self.calls = model_id, fail, 0

    def generate(
        self, question: str, passages: list[LabeledPassage], mode: GenerationMode
    ) -> GenerationResult:
        self.calls += 1
        if self.fail:
            raise GenerationError(f"{self.model_id} throttled")
        return GenerationResult(draft(answerable=False), self.model_id, None)


def test_fallback_model_answers_only_when_primary_fails() -> None:
    healthy = [Scripted("nova", fail=False), Scripted("deepseek", fail=False)]
    assert (
        FallbackAnswerGenerator(healthy).generate("q", [], GenerationMode.STANDARD).model_id
        == "nova"
    )
    assert healthy[1].calls == 0

    degraded = [Scripted("nova", fail=True), Scripted("deepseek", fail=False)]
    result = FallbackAnswerGenerator(degraded).generate("q", [], GenerationMode.STANDARD)
    assert result.model_id == "deepseek"


def test_all_models_failing_raises_generation_error() -> None:
    down = [Scripted("nova", fail=True), Scripted("deepseek", fail=True)]
    with pytest.raises(GenerationError, match="deepseek"):
        FallbackAnswerGenerator(down).generate("q", [], GenerationMode.STANDARD)
