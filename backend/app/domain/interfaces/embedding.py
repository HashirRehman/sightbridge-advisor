from typing import Protocol


class Embedder(Protocol):
    """Turns text into vectors. Documents and queries must use the same model."""

    @property
    def model_id(self) -> str: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
