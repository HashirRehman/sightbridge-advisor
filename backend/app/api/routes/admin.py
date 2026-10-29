from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies.auth import AdminUser
from app.api.dependencies.services import DbSession
from app.core.config import get_settings
from app.infrastructure.repositories.chat import AdminExchangeFilter, ChatRepository
from app.infrastructure.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.chat import AdminExchangeOut, AdminExchangePageOut, AuditSummaryOut
from app.schemas.knowledge_base import (
    ChunkOut,
    DocumentChunksOut,
    DocumentSummaryOut,
    EmbeddingSummaryOut,
    KnowledgeBaseOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/knowledge-base", response_model=KnowledgeBaseOut)
def knowledge_base(_: AdminUser, session: DbSession) -> KnowledgeBaseOut:
    repo = KnowledgeBaseRepository(session)
    counts = repo.embedding_counts()
    totals: dict[tuple[str, int], int] = {}
    documents = []
    for doc in repo.documents():
        embeddings = []
        for document_id, model, dimensions, count in counts:
            if document_id == doc.id:
                embeddings.append(
                    EmbeddingSummaryOut(model_id=model, dimensions=dimensions, chunk_count=count)
                )
                totals[model, dimensions] = totals.get((model, dimensions), 0) + count
        documents.append(
            DocumentSummaryOut(
                id=doc.id,
                title=doc.title,
                publisher=doc.publisher,
                source_url=doc.source_url,
                document_type=doc.document_type,
                topics=doc.topics,
                fetched_at=doc.fetched_at,
                updated_at=doc.updated_at,
                chunk_count=sum(e.chunk_count for e in embeddings),
                embeddings=embeddings,
            )
        )
    settings = get_settings()
    return KnowledgeBaseOut(
        document_count=len(documents),
        chunk_count=sum(totals.values()),
        documents=documents,
        query_embedding_model=(
            settings.bedrock_embedding_model_id
            if settings.embedding_provider == "bedrock"
            else f"local-hashing-v1-{settings.embedding_dimensions}"
        ),
        answer_model=(
            settings.bedrock_chat_model_id
            if settings.llm_provider == "bedrock"
            else "local-extractive-v1"
        ),
        fallback_answer_model=(
            settings.bedrock_fallback_chat_model_id or None
            if settings.llm_provider == "bedrock"
            else None
        ),
        embeddings=[
            EmbeddingSummaryOut(model_id=model, dimensions=dims, chunk_count=count)
            for (model, dims), count in sorted(totals.items())
        ],
    )


@router.get("/documents/{document_id}/chunks", response_model=DocumentChunksOut)
def document_chunks(
    document_id: str,
    _: AdminUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentChunksOut:
    repo = KnowledgeBaseRepository(session)
    doc = repo.document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    chunks, total = repo.chunks(document_id, offset, limit)
    return DocumentChunksOut(
        document_id=doc.id,
        title=doc.title,
        total=total,
        offset=offset,
        limit=limit,
        items=[
            ChunkOut(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                page_number=c.page_number,
                section=c.section,
                embedding_model=c.embedding_model,
                dimensions=len(c.embedding),
                embedding_preview=[float(v) for v in c.embedding[:8]],
            )
            for c in chunks
        ],
    )


@router.get("/exchanges", response_model=AdminExchangePageOut)
def list_exchanges(
    _: AdminUser,
    session: DbSession,
    status: Literal["answered", "refused", "unsupported", "failed"] | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    user: Annotated[str | None, Query(max_length=200)] = None,
    order: Literal["newest", "oldest"] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminExchangePageOut:
    repo = ChatRepository(session)
    items, total = repo.admin_list_exchanges(
        AdminExchangeFilter(
            status=status,
            search=q.strip() if q else None,
            user_email=user.strip() if user else None,
            newest_first=order == "newest",
            limit=limit,
            offset=offset,
        )
    )
    counts = repo.status_counts()
    return AdminExchangePageOut(
        items=[AdminExchangeOut.from_model(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
        summary=AuditSummaryOut(
            total=sum(counts.values()),
            answered=counts.get("answered", 0),
            refused=counts.get("refused", 0),
            unsupported=counts.get("unsupported", 0),
            failed=counts.get("failed", 0),
        ),
    )
