from typing import Any

from botocore.exceptions import ClientError
from langchain_aws import BedrockEmbeddings

from app.infrastructure.bedrock.invocation import invoke_bounded

# Transient service-side refusals. One immediate retry costs ~0.2 s and a few tokens, far
# less than failing the whole answer; anything else (auth, validation) fails at once.
_RETRYABLE = frozenset({"ThrottlingException", "ServiceUnavailableException"})


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
        try:
            return self._embed(text)
        except Exception as exc:
            if _error_code(exc) not in _RETRYABLE:
                raise
            return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def _embed(self, text: str) -> list[float]:
        return invoke_bounded(lambda: self._embeddings.embed_query(text), self._timeout_seconds)


def _error_code(exc: BaseException) -> str | None:
    # langchain-aws wraps SDK errors; look through the cause chain for the ClientError.
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ClientError):
            return str(current.response.get("Error", {}).get("Code"))
        current = current.__cause__ or current.__context__
    return None
