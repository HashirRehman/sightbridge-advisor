from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.application.chat.service import ChatService
from app.application.rag.service import AnswerService
from app.core.config import get_settings
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator
from app.infrastructure.database.session import get_db_session
from app.infrastructure.factory import build_answer_service, build_embedder, build_generator

DbSession = Annotated[Session, Depends(get_db_session)]


# Clients are created once per process (Lambda instance) and reused across requests.
@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())


@lru_cache
def get_generator() -> AnswerGenerator:
    return build_generator(get_settings())


def get_answer_service(session: DbSession) -> AnswerService:
    return build_answer_service(session, get_settings(), get_embedder(), get_generator())


def get_chat_service(
    session: DbSession, answers: Annotated[AnswerService, Depends(get_answer_service)]
) -> ChatService:
    settings = get_settings()
    return ChatService(
        session,
        answers,
        per_minute=settings.rate_limit_per_minute,
        per_hour=settings.rate_limit_per_hour,
    )
