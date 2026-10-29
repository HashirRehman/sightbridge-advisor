from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models.documents import RetrievedPassage
from app.infrastructure.database.models import Document, DocumentChunk


class PgVectorPassageRetriever:
    """Cosine-similarity search over chunks embedded with the *same* model as the query."""

    def __init__(self, session: Session, embedding_model: str) -> None:
        self._session = session
        self._embedding_model = embedding_model

    def search(
        self, query_embedding: list[float], *, top_k: int, min_similarity: float
    ) -> list[RetrievedPassage]:
        distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
        rows = self._session.execute(
            select(DocumentChunk, Document, distance)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.embedding_model == self._embedding_model)
            .order_by(distance)
            .limit(top_k)
        ).all()

        passages: list[RetrievedPassage] = []
        for chunk, document, dist in rows:
            similarity = 1.0 - float(dist)
            if similarity < min_similarity:
                break  # ordered best-first, so nothing after this qualifies
            passages.append(
                RetrievedPassage(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    title=document.title,
                    publisher=document.publisher,
                    source_url=document.source_url,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    content=chunk.content,
                    similarity=round(similarity, 4),
                )
            )
        return passages
