from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TextSection:
    """A contiguous run of extracted text with its location in the original document."""

    text: str
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_index: int
    content: str
    page_number: int | None
    section: str | None


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """A corpus passage returned by similarity search, with everything needed to cite it."""

    chunk_id: UUID
    document_id: str
    title: str
    publisher: str
    source_url: str
    page_number: int | None
    section: str | None
    content: str
    similarity: float

    @property
    def location(self) -> str | None:
        if self.page_number is not None:
            return f"p. {self.page_number}"
        return self.section
