import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.domain.models.identity import Principal
from app.infrastructure.database.models import Conversation, Exchange, User


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
