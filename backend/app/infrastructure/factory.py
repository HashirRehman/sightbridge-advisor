"""Composition root for infrastructure adapters chosen by configuration."""

import boto3
from sqlalchemy.orm import Session

from app.application.rag.fallback import FallbackAnswerGenerator
from app.application.rag.service import AnswerService
from app.core.config import Settings
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator
from app.domain.interfaces.safety import SafetyGate
from app.domain.interfaces.storage import ObjectStore
from app.infrastructure.bedrock.client import build_bedrock_runtime_client
from app.infrastructure.bedrock.conversation import (
    BedrockConversationResponder,
    BedrockConversationRouter,
)
from app.infrastructure.bedrock.efficient_conversation import EfficientConversationRouter
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.bedrock.generator import BedrockAnswerGenerator
from app.infrastructure.bedrock.grounding import BedrockGroundingGate
from app.infrastructure.bedrock.response_review import BedrockResponseReviewer
from app.infrastructure.bedrock.safety import BedrockSafetyGate, LocalSafetyGate
from app.infrastructure.repositories.knowledge_base import KnowledgeBaseRepository
from app.infrastructure.retrieval.local_embedder import LocalHashingEmbedder
from app.infrastructure.retrieval.local_generator import LocalExtractiveGenerator
from app.infrastructure.retrieval.pgvector_retriever import PgVectorPassageRetriever
from app.infrastructure.storage import NullObjectStore, S3ObjectStore


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "local":
        return LocalHashingEmbedder(settings.embedding_dimensions)
    return BedrockTitanEmbedder(
        build_bedrock_runtime_client(settings),
        settings.bedrock_embedding_model_id,
        settings.embedding_dimensions,
        settings.embedding_timeout_seconds,
    )


def build_object_store(settings: Settings) -> ObjectStore:
    if not settings.documents_bucket:
        return NullObjectStore()
    return S3ObjectStore(
        boto3.client("s3", region_name=settings.aws_region), settings.documents_bucket
    )


def build_generator(
    settings: Settings,
    *,
    primary_timeout: float | None = None,
    fallback_timeout: float | None = None,
) -> AnswerGenerator:
    if settings.llm_provider == "local":
        return LocalExtractiveGenerator()
    client = build_bedrock_runtime_client(settings)
    model_ids = [settings.bedrock_chat_model_id]
    if settings.bedrock_fallback_chat_model_id:
        model_ids.append(settings.bedrock_fallback_chat_model_id)
    generators: list[AnswerGenerator] = [
        BedrockAnswerGenerator(
            client,
            model_id,
            settings.max_output_tokens,
            settings.aws_region,
            (primary_timeout or settings.generation_timeout_seconds)
            if index == 0
            else (fallback_timeout or settings.answer_timeout_seconds),
        )
        for index, model_id in enumerate(model_ids)
    ]
    return generators[0] if len(generators) == 1 else FallbackAnswerGenerator(generators)


def build_answer_service(
    session: Session,
    settings: Settings,
    embedder: Embedder,
    generator: AnswerGenerator,
    safety_gate: SafetyGate | None = None,
    *,
    timeout_seconds: float | None = None,
) -> AnswerService:
    inventory = (
        [
            {"title": d.title[:200], "publisher": d.publisher[:150]}
            for d in KnowledgeBaseRepository(session).documents()[:30]
        ]
        if settings.llm_provider == "bedrock"
        else []
    )
    optimized = settings.llm_provider == "bedrock" and settings.rag_execution_mode == "optimized"
    return AnswerService(
        embedder,
        PgVectorPassageRetriever(session, embedder.model_id),
        generator,
        top_k=settings.retrieval_top_k,
        min_similarity=settings.retrieval_min_similarity,
        timeout_seconds=timeout_seconds or settings.answer_timeout_seconds,
        safety_gate=None if optimized else (safety_gate or build_safety_gate(settings)),
        grounding_gate=BedrockGroundingGate(
            build_bedrock_runtime_client(settings),
            settings.safety_model_id,
            settings.aws_region,
        )
        if settings.llm_provider == "bedrock" and not optimized
        else None,
        response_reviewer=BedrockResponseReviewer(
            build_bedrock_runtime_client(settings),
            settings.safety_model_id,
            settings.aws_region,
            inventory,
        )
        if optimized
        else None,
        router=(EfficientConversationRouter if optimized else BedrockConversationRouter)(
            build_bedrock_runtime_client(settings),
            settings.bedrock_chat_model_id,
            settings.aws_region,
            inventory,
        )
        if settings.llm_provider == "bedrock"
        else None,
        responder=BedrockConversationResponder(
            build_bedrock_runtime_client(settings),
            settings.bedrock_chat_model_id,
            settings.aws_region,
            inventory,
            review_model_id=None if optimized else settings.bedrock_chat_model_id,
        )
        if settings.llm_provider == "bedrock"
        else None,
    )


def build_safety_gate(settings: Settings) -> SafetyGate:
    if settings.llm_provider == "local":
        return LocalSafetyGate()
    return BedrockSafetyGate(
        build_bedrock_runtime_client(settings),
        settings.safety_model_id,
        settings.aws_region,
        settings.safety_timeout_seconds,
        advice_reviewer=BedrockSafetyGate(
            build_bedrock_runtime_client(settings),
            settings.bedrock_chat_model_id,
            settings.aws_region,
            settings.safety_timeout_seconds,
        ),
    )
