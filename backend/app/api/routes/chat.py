import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.dependencies.auth import CurrentUser
from app.api.dependencies.services import DbSession, get_answer_dispatcher, get_chat_service
from app.application.chat.service import (
    ActivityConflictError,
    ActivityExpiredError,
    ChatService,
    ConversationNotFoundError,
    RateLimitExceededError,
)
from app.core.logging import request_id_var
from app.domain.interfaces.dispatch import AnswerDispatcher, DispatchError
from app.infrastructure.database.models import Exchange
from app.infrastructure.repositories.activity import ActivityRepository
from app.infrastructure.repositories.chat import ChatRepository
from app.schemas.chat import (
    ActivityOut,
    ChatRequest,
    ConversationDetailOut,
    ConversationSummaryOut,
    ExchangeOut,
    MeOut,
)

router = APIRouter(prefix="/api", tags=["advisor"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return MeOut(user_id=user.user_id, email=user.email, is_admin=user.is_admin)


@router.post("/chat", response_model=ExchangeOut | ActivityOut)
def chat(
    body: ChatRequest,
    user: CurrentUser,
    service: Annotated[ChatService, Depends(get_chat_service)],
    dispatcher: Annotated[AnswerDispatcher, Depends(get_answer_dispatcher)],
    response: Response,
) -> ExchangeOut | ActivityOut:
    try:
        if body.background:
            result = service.start(
                user,
                body.question,
                body.conversation_id,
                request_id_var.get(),
                body.activity_id,
                dispatcher,
            )
            if isinstance(result, Exchange):
                return ExchangeOut.from_model(result)
            response.status_code = 202
            return ActivityOut(
                id=result.id,
                steps=result.steps,
                finished=result.finished_at is not None,
                exchange_id=result.exchange_id,
            )
        exchange = service.ask(
            user, body.question, body.conversation_id, request_id_var.get(), body.activity_id
        )
    except ConversationNotFoundError:
        raise _NOT_FOUND from None
    except DispatchError:
        raise HTTPException(
            status_code=503, detail="Couldn't start this request. Please try again."
        ) from None
    except ActivityExpiredError:
        raise HTTPException(
            status_code=410, detail="This request expired. Please send your question again."
        ) from None
    except ActivityConflictError:
        raise HTTPException(
            status_code=409,
            detail="This request is already being processed or has different content.",
        ) from None
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many questions in a short period. Please wait and try again.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None
    return ExchangeOut.from_model(exchange)


@router.get("/chat/activity/{activity_id}", response_model=ActivityOut)
def chat_activity(activity_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ActivityOut:
    activity = ActivityRepository(session).get(activity_id, user.user_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found.")
    return ActivityOut(
        id=activity.id,
        steps=activity.steps,
        finished=activity.finished_at is not None
        or activity.created_at < datetime.now(UTC) - timedelta(seconds=130),
        exchange_id=activity.exchange_id,
    )


@router.get("/chat/activity/{activity_id}/result", response_model=ExchangeOut)
def chat_result(activity_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ExchangeOut:
    activity = ActivityRepository(session).get(activity_id, user.user_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found.")
    if activity.exchange_id is None:
        if activity.finished_at is not None or activity.created_at < datetime.now(UTC) - timedelta(
            seconds=130
        ):
            raise HTTPException(
                status_code=410,
                detail="This request could not finish. Please send your question again.",
            )
        raise HTTPException(status_code=409, detail="This request is still being processed.")
    exchange = session.get(Exchange, activity.exchange_id)
    if exchange is None or exchange.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Result not found.")
    return ExchangeOut.from_model(exchange)


@router.get("/conversations", response_model=list[ConversationSummaryOut])
def list_conversations(user: CurrentUser, session: DbSession) -> list[ConversationSummaryOut]:
    return [
        ConversationSummaryOut(
            id=s.conversation.id,
            title=s.conversation.title,
            exchange_count=s.exchange_count,
            updated_at=s.conversation.updated_at,
        )
        for s in ChatRepository(session).list_conversations(user.user_id)
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ConversationDetailOut:
    repo = ChatRepository(session)
    # Ownership is part of the query: another user's conversation is simply "not found".
    conversation = repo.get_owned_conversation(conversation_id, user.user_id)
    if conversation is None:
        raise _NOT_FOUND
    return ConversationDetailOut.from_model(conversation, repo.list_exchanges(conversation.id))
