import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, union_all, update
from sqlalchemy.orm import Session

from app.infrastructure.database.models import AnswerActivity, Exchange


class ActivityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_user(self, user_id: str) -> None:
        key = int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], signed=True)
        self.session.execute(select(func.pg_advisory_xact_lock(key)))

    def lock_capacity(self) -> None:
        self.session.execute(select(func.pg_advisory_xact_lock(7419, 1)))

    def active_total(self, now: datetime) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(AnswerActivity)
                .where(
                    AnswerActivity.finished_at.is_(None),
                    AnswerActivity.created_at >= now - timedelta(seconds=45),
                )
            )
            or 0
        )

    def get(self, activity_id: uuid.UUID, user_id: str) -> AnswerActivity | None:
        return self.session.scalar(
            select(AnswerActivity).where(
                AnswerActivity.id == activity_id, AnswerActivity.user_id == user_id
            )
        )

    def requests_since(self, user_id: str, since: datetime) -> int:
        # One database snapshot avoids under-counting a request that finishes between
        # separate reads of completed exchanges and in-flight activity.
        requests = union_all(
            select(Exchange.id).where(Exchange.user_id == user_id, Exchange.created_at >= since),
            select(AnswerActivity.id).where(
                AnswerActivity.user_id == user_id,
                AnswerActivity.exchange_id.is_(None),
                AnswerActivity.created_at >= since,
            ),
        ).subquery()
        return self.session.scalar(select(func.count()).select_from(requests)) or 0

    def active(self, user_id: str, now: datetime) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(AnswerActivity)
                .where(
                    AnswerActivity.user_id == user_id,
                    AnswerActivity.finished_at.is_(None),
                    AnswerActivity.created_at >= now - timedelta(seconds=45),
                )
            )
            or 0
        )

    def reserve(self, activity_id: uuid.UUID, user_id: str, fingerprint: str) -> AnswerActivity:
        # Progress expires after a day; the final trace remains in the immutable exchange.
        self.session.execute(
            delete(AnswerActivity).where(
                AnswerActivity.created_at < datetime.now(UTC) - timedelta(days=1)
            )
        )
        record = AnswerActivity(id=activity_id, user_id=user_id, fingerprint=fingerprint, steps=[])
        self.session.add(record)
        self.session.flush()
        return record

    def publish(self, activity_id: uuid.UUID, user_id: str, steps: list[dict[str, Any]]) -> None:
        self.session.execute(
            update(AnswerActivity)
            .where(
                AnswerActivity.id == activity_id,
                AnswerActivity.user_id == user_id,
                AnswerActivity.finished_at.is_(None),
            )
            .values(steps=steps)
        )
        self.session.commit()
