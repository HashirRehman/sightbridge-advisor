from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under `key`; return a stable reference (e.g. `s3://bucket/key`)."""
        ...
