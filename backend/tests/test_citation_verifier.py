from app.domain.policies.citation_verifier import MAX_EXPLANATION_CLAIMS, verify_answer
from tests.factories import DST_TEXT, IDENTIFICATION_TEXT, claim, draft, passage

P1 = passage("P1", IDENTIFICATION_TEXT)
P2 = passage("P2", DST_TEXT, page=3)
VALID_45 = ("P1", "must be identified within 45 days after the date you transfer")


def test_grounded_claim_is_kept_with_deep_linked_citation() -> None:
    result = verify_answer(
        draft(claim("Replacement property must be identified within 45 days.", VALID_45)), [P1]
    )

    assert [c.text for c in result.explanation] == [
        "Replacement property must be identified within 45 days."
    ]
    (citation,) = result.citations
    assert citation.number == 1 and citation.document_id == "doc-p1"
    assert citation.url.startswith("https://www.irs.gov/p1#:~:text=must%20be%20identified")
    assert result.dropped_claims == 0


def test_fabricated_passage_id_and_fabricated_quote_are_rejected() -> None:
    result = verify_answer(
        draft(
            claim("Property must be identified within 45 days.", ("P9", VALID_45[1])),
            claim(
                "Replacement property must be identified within 60 days.",
                ("P1", "must be identified within 60 days after the transfer"),
            ),
        ),
        [P1],
    )

    assert result.explanation == []
    assert result.citations == []
    assert (result.dropped_claims, result.rejected_citations) == (2, 2)


def test_quote_matching_tolerates_case_and_whitespace_differences() -> None:
    sloppy = "the Replacement   property must be received within 180 days, or by the due date"
    result = verify_answer(
        draft(
            claim("It must be received within 180 days or by the return due date.", ("p1", sloppy))
        ),
        [P1],
    )
    assert len(result.explanation) == 1


def test_claim_unrelated_to_its_real_quote_is_dropped() -> None:
    unrelated = claim("Opportunity zone funds require a ten year holding period.", VALID_45)
    assert verify_answer(draft(unrelated), [P1]).explanation == []


def test_advice_language_is_dropped_even_when_cited() -> None:
    advice = claim("You should invest within 45 days to identify property.", VALID_45)
    assert verify_answer(draft(advice), [P1]).explanation == []


def test_citations_are_numbered_by_first_use_and_shared_across_claims() -> None:
    dst_quote = ("P2", "Interests in the trust may be treated as interests in real property")
    result = verify_answer(
        draft(
            claim("DST interests may be treated as real property for section 1031.", dst_quote),
            claim("Replacement property must be identified within 45 days.", VALID_45),
            considerations=(claim("A DST interest can be treated as real property.", dst_quote),),
        ),
        [P1, P2],
    )

    assert [c.citation_numbers for c in result.explanation] == [(1,), (2,)]
    assert result.key_considerations[0].citation_numbers == (1,)
    assert result.citations[0].url == "https://www.irs.gov/p2#page=3"
    assert result.citations[0].location == "p. 3"


def test_long_detailed_answers_are_kept_up_to_the_backstop() -> None:
    quote = "must be identified within 45 days after the date you transfer"
    steps = [
        claim(f"Step {i}: identify the property within 45 days.", ("P1", quote)) for i in range(12)
    ]
    result = verify_answer(draft(*steps), [passage("P1", IDENTIFICATION_TEXT)])
    assert len(result.explanation) == MAX_EXPLANATION_CLAIMS == 10
