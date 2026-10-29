import pytest

from app.domain.policies.retrieval_query import expand_retrieval_query


@pytest.mark.parametrize(
    ("question", "expected_terms"),
    [
        (
            "How does a 721 differ from a 1031?",
            ("UPREIT operating partnership", "like-kind real estate"),
        ),
        ("What risks do dsts have?", ("Delaware statutory trust",)),
        ("What does a qualified intermediary do?", ("qualified intermediary",)),
    ],
)
def test_abbreviations_expand_without_removing_question_terms(
    question: str, expected_terms: tuple[str, ...]
) -> None:
    expanded = expand_retrieval_query(question)
    assert all(term in expanded for term in expected_terms)
