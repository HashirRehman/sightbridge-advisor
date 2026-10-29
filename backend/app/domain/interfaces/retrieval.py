from typing import Protocol

from app.domain.models.documents import RetrievedPassage


class PassageRetriever(Protocol):
    def search(
        self, query_embedding: list[float], *, top_k: int, min_similarity: float
    ) -> list[RetrievedPassage]:
        """Return up to `top_k` passages with cosine similarity >= `min_similarity`, best first."""
        ...
