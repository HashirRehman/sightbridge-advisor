"""Split located sections into overlapping chunks that never cross a page or heading boundary
(so every chunk has exactly one citable location)."""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.domain.models.documents import ChunkDraft, TextSection

# Bump when chunking behaviour changes: it is part of the ingest signature, so a bump
# forces re-ingestion of every document.
CHUNKER_VERSION = "v1-1200-200"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
# Sections shorter than this are merged with the following section on the same page.
MIN_SECTION_CHARS = 300


def _merge_small_sections(sections: list[TextSection]) -> list[TextSection]:
    merged: list[TextSection] = []
    pending: TextSection | None = None
    for current in sections:
        if pending is not None:
            if pending.page_number == current.page_number:
                current = TextSection(
                    text=f"{pending.text}\n{current.text}",
                    page_number=current.page_number,
                    section=pending.section or current.section,
                )
            else:
                merged.append(pending)
            pending = None
        if len(current.text) < MIN_SECTION_CHARS:
            pending = current
        else:
            merged.append(current)
    if pending is not None:
        merged.append(pending)
    return merged


def chunk_sections(sections: list[TextSection]) -> list[ChunkDraft]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", "; ", " ", ""],
    )
    chunks: list[ChunkDraft] = []
    for section in _merge_small_sections(sections):
        for piece in splitter.split_text(section.text):
            content = piece.strip()
            if content:
                chunks.append(
                    ChunkDraft(
                        chunk_index=len(chunks),
                        content=content,
                        page_number=section.page_number,
                        section=section.section,
                    )
                )
    return chunks


def embedding_text(title: str, chunk: ChunkDraft) -> str:
    """Text sent to the embedder: a short context header improves retrieval of terse passages.
    The stored/displayed content stays the original chunk text."""
    location = chunk.section or (f"page {chunk.page_number}" if chunk.page_number else "")
    header = f"{title} — {location}" if location else title
    return f"{header}\n\n{chunk.content}"
