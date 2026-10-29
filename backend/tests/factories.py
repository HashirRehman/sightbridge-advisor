from uuid import uuid4

from app.domain.models.answers import AnswerDraft, DraftCitation, DraftClaim, LabeledPassage

IDENTIFICATION_TEXT = (
    "The replacement property must be identified within 45 days after the date you transfer "
    "the property given up. The replacement property must be received within 180 days, or by "
    "the due date of your tax return, whichever is earlier."
)
DST_TEXT = (
    "A Delaware statutory trust is an unincorporated association recognized as an entity "
    "separate from its owners. Interests in the trust may be treated as interests in real "
    "property for purposes of section 1031."
)


def passage(label: str, content: str, *, page: int | None = None) -> LabeledPassage:
    return LabeledPassage(
        label=label,
        chunk_id=uuid4(),
        document_id=f"doc-{label.lower()}",
        title=f"Source {label}",
        publisher="Internal Revenue Service",
        source_url=f"https://www.irs.gov/{label.lower()}",
        page_number=page,
        section=None if page else "Deferred exchanges",
        content=content,
        similarity=0.8,
    )


def claim(text: str, *cites: tuple[str, str]) -> DraftClaim:
    return DraftClaim(text=text, citations=[DraftCitation(passage_id=p, quote=q) for p, q in cites])


def draft(
    *explanation: DraftClaim, considerations: tuple[DraftClaim, ...] = (), answerable: bool = True
) -> AnswerDraft:
    return AnswerDraft(
        answerable=answerable,
        explanation=list(explanation),
        key_considerations=list(considerations),
    )
