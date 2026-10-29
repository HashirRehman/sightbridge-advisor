from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EmbeddingSummaryOut(BaseModel):
    model_id: str
    dimensions: int
    chunk_count: int


class DocumentSummaryOut(BaseModel):
    id: str
    title: str
    publisher: str
    source_url: str
    document_type: str
    topics: list[str]
    fetched_at: datetime
    updated_at: datetime
    chunk_count: int
    embeddings: list[EmbeddingSummaryOut]


class KnowledgeBaseOut(BaseModel):
    document_count: int
    chunk_count: int
    query_embedding_model: str
    answer_model: str
    fallback_answer_model: str | None
    embeddings: list[EmbeddingSummaryOut]
    documents: list[DocumentSummaryOut]


class ChunkOut(BaseModel):
    id: UUID
    chunk_index: int
    content: str
    page_number: int | None
    section: str | None
    embedding_model: str
    dimensions: int
    embedding_preview: list[float]


class DocumentChunksOut(BaseModel):
    document_id: str
    title: str
    total: int
    offset: int
    limit: int
    items: list[ChunkOut]
