import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.domain.models.answers import Citation
from app.domain.models.documents import RetrievedPassage
from app.domain.models.identity import Principal
from app.domain.policies.citation_verifier import quote_matches
from app.infrastructure.database.models import Conversation, Document, DocumentChunk, Exchange, User

# Answered turns a restatement may draw evidence from, and the passage cap it shares.
REUSE_DEPTH = 3
MAX_REUSED_PASSAGES = 6


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: Conversation
    exchange_count: int


@dataclass(frozen=True, slots=True)
class AdminExchangeFilter:
    status: str | None = None
    search: str | None = None  # substring of the question
    user_email: str | None = None  # substring of the asker's email
    newest_first: bool = True
    limit: int = 25
    offset: int = 0


class ChatRepository:
    """Conversations and the exchange audit log. The caller owns the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_user(self, principal: Principal) -> None:
        stmt = insert(User).values(
            id=principal.user_id, email=principal.email, is_admin=principal.is_admin
        )
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[User.id],
                set_={
                    "email": stmt.excluded.email,
                    "is_admin": stmt.excluded.is_admin,
                    "last_seen_at": func.now(),
                },
            )
        )

    def get_owned_conversation(
        self, conversation_id: uuid.UUID, user_id: str
    ) -> Conversation | None:
        return self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            )
        )

    def create_conversation(self, user_id: str, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self._session.add(conversation)
        self._session.flush()
        return conversation

    def add_exchange(self, conversation: Conversation, **fields: Any) -> Exchange:
        exchange = Exchange(conversation_id=conversation.id, **fields)
        self._session.add(exchange)
        conversation.updated_at = datetime.now(UTC)
        self._session.flush()
        self._session.refresh(exchange)
        return exchange

    def count_exchanges_since(self, user_id: str, since: datetime) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(Exchange)
                .where(Exchange.user_id == user_id, Exchange.created_at >= since)
            )
            or 0
        )

    def list_conversations(self, user_id: str, limit: int = 50) -> list[ConversationSummary]:
        counts = (
            select(Exchange.conversation_id, func.count().label("n"))
            .group_by(Exchange.conversation_id)
            .subquery()
        )
        rows = self._session.execute(
            select(Conversation, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        ).all()
        return [ConversationSummary(c, n) for c, n in rows]

    def recent_context(self, conversation_id: uuid.UUID, user_id: str) -> list[dict[str, str]]:
        """Bounded context, restricted to both the selected thread and its owner."""
        rows = list(
            self._session.scalars(
                select(Exchange)
                .where(Exchange.conversation_id == conversation_id, Exchange.user_id == user_id)
                .order_by(Exchange.created_at.desc(), Exchange.id.desc())
                .limit(4)
            )
        )
        return [
            {"question": row.question[:1000], "answer": row.answer_text[:1200]}
            for row in reversed(rows)
            if row.status != "failed" and row.reason != "sensitive_data"
        ]

    def reusable_evidence(
        self, conversation_id: uuid.UUID, user_id: str, embedding_model: str
    ) -> list[RetrievedPassage]:
        """Current public chunks cited by the most recent owned, answered exchanges.

        Restatements ("I still don't get it") need the material behind the explanation, not
        only the last rewrite, which cites fewer passages each time. The latest answer's
        passages come first; up to REUSE_DEPTH earlier answers fill the remaining room.
        Collection stops at the first exchange that isn't a clean answer, so a refusal is
        never bridged. Never accept client-supplied chunk IDs or trust generated prose.
        """
        passages: list[RetrievedPassage] = []
        for row in self._recent_exchanges(conversation_id, user_id, REUSE_DEPTH):
            current = self._current_passages(row, embedding_model)
            if current is None:
                break
            for passage in current:
                if all(p.chunk_id != passage.chunk_id for p in passages):
                    passages.append(passage)
        return passages[:MAX_REUSED_PASSAGES]

    def reusable_citations(
        self, conversation_id: uuid.UUID, user_id: str, embedding_model: str
    ) -> list[Citation]:
        """The latest answer's original citations, only when its chunks pass reuse checks."""
        latest = self._recent_exchanges(conversation_id, user_id, 1)
        current = self._current_passages(latest[0], embedding_model) if latest else None
        if not current:
            return []
        return [
            Citation(
                number=index,
                chunk_id=passage.chunk_id,
                document_id=passage.document_id,
                title=passage.title,
                publisher=passage.publisher,
                url=source.get("url", passage.source_url),
                source_url=passage.source_url,
                location=passage.location,
                quotes=tuple(source["quotes"]),
            )
            for index, (source, passage) in enumerate(
                zip(latest[0].sources[:MAX_REUSED_PASSAGES], current, strict=True), start=1
            )
        ]

    def _recent_exchanges(
        self, conversation_id: uuid.UUID, user_id: str, limit: int
    ) -> list[Exchange]:
        return list(
            self._session.scalars(
                select(Exchange)
                .where(Exchange.conversation_id == conversation_id, Exchange.user_id == user_id)
                .order_by(Exchange.created_at.desc(), Exchange.id.desc())
                .limit(limit)
            )
        )

    def _current_passages(
        self, row: Exchange, embedding_model: str
    ) -> list[RetrievedPassage] | None:
        """Every cited chunk, unchanged since the answer and still quoting exactly; else None.

        Re-ingestion of any cited document invalidates the whole answer as a reuse source.
        """
        if row.status != "answered" or not row.sources:
            return None
        result = []
        for source in row.sources[:MAX_REUSED_PASSAGES]:
            try:
                chunk_id = uuid.UUID(source["chunk_id"])
            except (ValueError, KeyError, TypeError):
                return None
            pair = self._session.execute(
                select(DocumentChunk, Document)
                .join(Document)
                .where(
                    DocumentChunk.id == chunk_id,
                    DocumentChunk.embedding_model == embedding_model,
                )
            ).first()
            if pair is None:
                return None
            chunk, document = pair
            if (
                document.updated_at > row.created_at
                or document.id != source.get("document_id")
                or not source.get("quotes")
                or not all(quote_matches(q, chunk.content) for q in source["quotes"])
            ):
                return None
            result.append(
                RetrievedPassage(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    title=document.title,
                    publisher=document.publisher,
                    source_url=document.source_url,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    content=chunk.content,
                    similarity=1.0,
                )
            )
        return result

    def list_exchanges(self, conversation_id: uuid.UUID) -> list[Exchange]:
        return list(
            self._session.scalars(
                select(Exchange)
                .where(Exchange.conversation_id == conversation_id)
                .order_by(Exchange.created_at)
            )
        )

    def admin_list_exchanges(self, f: AdminExchangeFilter) -> tuple[list[Exchange], int]:
        query = select(Exchange).join(User, Exchange.user_id == User.id)
        if f.status:
            query = query.where(Exchange.status == f.status)
        if f.search:
            query = query.where(Exchange.question.icontains(f.search, autoescape=True))
        if f.user_email:
            query = query.where(User.email.icontains(f.user_email, autoescape=True))

        total = self._session.scalar(select(func.count()).select_from(query.subquery())) or 0
        order = Exchange.created_at.desc() if f.newest_first else Exchange.created_at.asc()
        rows = self._session.scalars(
            query.options(joinedload(Exchange.user))
            .order_by(order, Exchange.id)
            .limit(f.limit)
            .offset(f.offset)
        ).all()
        return list(rows), total

    def status_counts(self) -> dict[str, int]:
        rows = self._session.execute(
            select(Exchange.status, func.count()).group_by(Exchange.status)
        ).all()
        return {status: count for status, count in rows}
