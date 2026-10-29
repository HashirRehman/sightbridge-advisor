"""Ingest the curated corpus: fetch → extract → chunk → embed → store.

    uv run python -m scripts.ingest              # idempotent; unchanged sources are skipped
    uv run python -m scripts.ingest --refresh    # re-download originals from publishers
    uv run python -m scripts.ingest --force      # re-embed even if unchanged

Writes data/sources.lock.json (content hash + retrieval date per source) for provenance.
Exit code is non-zero if any source failed.
"""

import argparse
import json
import sys

import httpx

from app.application.ingestion.fetcher import SourceFetcher
from app.application.ingestion.manifest import DEFAULT_MANIFEST, load_manifest
from app.application.ingestion.service import IngestionService
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.factory import build_embedder, build_object_store

DATA_DIR = DEFAULT_MANIFEST.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download originals")
    parser.add_argument("--force", action="store_true", help="re-embed unchanged sources")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    manifest = load_manifest()

    with httpx.Client(timeout=30.0, follow_redirects=True) as http:
        service = IngestionService(
            session_factory=get_session_factory(),
            embedder=build_embedder(settings),
            fetcher=SourceFetcher(http, DATA_DIR / "raw", refresh=args.refresh),
            object_store=build_object_store(settings),
        )
        results = service.ingest_all(manifest, force=args.force)

    lock_path = DATA_DIR / "sources.lock.json"
    previous = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    lock = {}
    for spec, result in zip(manifest.sources, results, strict=True):
        print(
            f"{result.status:9} {spec.source_id:42} chunks={result.chunk_count or '-'}"
            + (f"  error={result.error}" if result.error else "")
        )
        if result.sha256 and result.fetched_at:
            lock[spec.source_id] = {
                "url": str(spec.url),
                "sha256": result.sha256,
                "retrieved_at": result.fetched_at.date().isoformat(),
            }
        elif spec.source_id in previous:
            lock[spec.source_id] = previous[spec.source_id]
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")

    failed = [r for r in results if r.status == "failed"]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} sources ok "
        f"(embedding model: {build_embedder(settings).model_id})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
