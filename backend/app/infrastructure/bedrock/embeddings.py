from typing import Any

from langchain_aws import BedrockEmbeddings

from app.infrastructure.bedrock.invocation import invoke_bounded


class BedrockTitanEmbedder:
    """Amazon Titan Text Embeddings V2 via langchain-aws. Vectors are unit-normalised so
    pgvector cosine distance is well behaved."""

    def __init__(
        self, client: Any, model_id: str, dimensions: int, timeout_seconds: float = 4.0
    ) -> None:
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._embeddings = BedrockEmbeddings(
            client=client,
            model_id=model_id,
            dimensions=dimensions,
            model_kwargs={"normalize": True},
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def embed_query(self, text: str) -> list[float]:
        return invoke_bounded(lambda: self._embeddings.embed_query(text), self._timeout_seconds)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)
