from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import Document, DocumentChunk


class KnowledgeBaseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def documents(self) -> list[Document]:
        return list(
            self._session.scalars(select(Document).order_by(Document.publisher, Document.title))
        )

    def embedding_counts(self) -> list[tuple[str, str, int, int]]:
        dimensions = func.vector_dims(DocumentChunk.embedding)
        rows = self._session.execute(
            select(
                DocumentChunk.document_id,
                DocumentChunk.embedding_model,
                dimensions,
                func.count(),
            )
            .group_by(DocumentChunk.document_id, DocumentChunk.embedding_model, dimensions)
            .order_by(DocumentChunk.document_id, DocumentChunk.embedding_model, dimensions)
        )
        return [(str(doc), str(model), int(dims), int(count)) for doc, model, dims, count in rows]

    def document(self, document_id: str) -> Document | None:
        return self._session.get(Document, document_id)

    def chunks(self, document_id: str, offset: int, limit: int) -> tuple[list[DocumentChunk], int]:
        query = select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        total = self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = self._session.scalars(
            query.order_by(DocumentChunk.chunk_index).offset(offset).limit(limit)
        )
        return list(items), total
