"""Composition root for infrastructure adapters chosen by configuration."""

import boto3
from sqlalchemy.orm import Session

from app.application.rag.fallback import FallbackAnswerGenerator
from app.application.rag.service import AnswerService
from app.core.config import Settings
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.generation import AnswerGenerator
from app.domain.interfaces.storage import ObjectStore
from app.infrastructure.bedrock.client import build_bedrock_runtime_client
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.bedrock.generator import BedrockAnswerGenerator
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


def build_generator(settings: Settings) -> AnswerGenerator:
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
            settings.generation_timeout_seconds if index == 0 else settings.answer_timeout_seconds,
        )
        for index, model_id in enumerate(model_ids)
    ]
    return generators[0] if len(generators) == 1 else FallbackAnswerGenerator(generators)


def build_answer_service(
    session: Session, settings: Settings, embedder: Embedder, generator: AnswerGenerator
) -> AnswerService:
    return AnswerService(
        embedder,
        PgVectorPassageRetriever(session, embedder.model_id),
        generator,
        top_k=settings.retrieval_top_k,
        min_similarity=settings.retrieval_min_similarity,
        timeout_seconds=settings.answer_timeout_seconds,
    )
