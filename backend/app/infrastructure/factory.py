"""Composition root for infrastructure adapters chosen by configuration."""

import boto3

from app.core.config import Settings
from app.domain.interfaces.embedding import Embedder
from app.domain.interfaces.storage import ObjectStore
from app.infrastructure.bedrock.client import build_bedrock_runtime_client
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.retrieval.local_embedder import LocalHashingEmbedder
from app.infrastructure.storage import NullObjectStore, S3ObjectStore


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "local":
        return LocalHashingEmbedder(settings.embedding_dimensions)
    return BedrockTitanEmbedder(
        build_bedrock_runtime_client(settings),
        settings.bedrock_embedding_model_id,
        settings.embedding_dimensions,
    )


def build_object_store(settings: Settings) -> ObjectStore:
    if not settings.documents_bucket:
        return NullObjectStore()
    return S3ObjectStore(
        boto3.client("s3", region_name=settings.aws_region), settings.documents_bucket
    )
