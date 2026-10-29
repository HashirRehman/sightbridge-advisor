from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.dependencies.auth import AdminUser
from app.api.dependencies.services import DbSession
from app.infrastructure.repositories.chat import AdminExchangeFilter, ChatRepository
from app.schemas.chat import AdminExchangeOut, AdminExchangePageOut, AuditSummaryOut

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
