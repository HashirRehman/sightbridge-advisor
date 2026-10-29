from typing import Any


class S3ObjectStore:
    """Private S3 bucket for original source files (SSE-S3 encrypted at rest)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        return f"s3://{self._bucket}/{key}"


class NullObjectStore:
    """Used locally when no bucket is configured; originals stay in data/raw/."""

    def put(self, key: str, data: bytes, content_type: str) -> str:
        return f"local://{key}"
