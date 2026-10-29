import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.application.ingestion.manifest import SourceSpec

MAX_SOURCE_BYTES = 15 * 1024 * 1024
_USER_AGENT = "Mozilla/5.0 (compatible; SightbridgeAdvisorIngest/1.0; curated public sources)"


class FetchError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FetchedSource:
    data: bytes
    sha256: str
    fetched_at: datetime
    content_type: str


class SourceFetcher:
    """Downloads approved manifest URLs only, caching originals under `raw_dir` so
    re-runs are reproducible even if a publisher changes or removes a page."""

    def __init__(self, client: httpx.Client, raw_dir: Path, *, refresh: bool = False) -> None:
        self._client = client
        self._raw_dir = raw_dir
        self._refresh = refresh

    def fetch(self, spec: SourceSpec) -> FetchedSource:
        path = self._raw_dir / f"{spec.source_id}.{spec.document_type}"
        content_type = "application/pdf" if spec.document_type == "pdf" else "text/html"

        if path.exists() and not self._refresh:
            data = path.read_bytes()
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        else:
            data = self._download(spec)
            self._raw_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            fetched_at = datetime.now(UTC)

        self._validate(spec, data)
        return FetchedSource(
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            fetched_at=fetched_at,
            content_type=content_type,
        )

    def _download(self, spec: SourceSpec) -> bytes:
        try:
            response = self._client.get(str(spec.url), headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FetchError(f"download failed: {exc}") from exc
        if len(response.content) > MAX_SOURCE_BYTES:
            raise FetchError("source exceeds size limit")
        return response.content

    @staticmethod
    def _validate(spec: SourceSpec, data: bytes) -> None:
        if spec.document_type == "pdf" and not data.startswith(b"%PDF"):
            raise FetchError("expected a PDF but received something else")
        if spec.document_type == "html" and b"<html" not in data[:5000].lower():
            raise FetchError("expected an HTML page but received something else")
