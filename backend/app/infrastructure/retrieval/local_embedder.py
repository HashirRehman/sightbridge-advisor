"""Deterministic lexical embedder for local development and tests — never production.

Feature-hashes stemmed unigrams and bigrams into the same 1024-d space as Titan V2, so the
schema, ingestion and pgvector search paths are exercised end-to-end without AWS. It captures
keyword overlap, not meaning; real retrieval quality is measured with Bedrock embeddings.
"""

import hashlib
import itertools
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOPWORDS = frozenset(
    (
        "a an and are as at be by can do does for from has have how i in is it its may of on or "
        "s that the their there this to was what when which who will with would you your"
    ).split()
)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class LocalHashingEmbedder:
    def __init__(self, dimensions: int = 1024) -> None:
        self._dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"local-hashing-v1-{self._dimensions}"

    def _embed(self, text: str) -> list[float]:
        tokens = _tokens(text)
        features = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        vector = [0.0] * self._dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # pgvector cosine distance is undefined for the zero vector.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]
