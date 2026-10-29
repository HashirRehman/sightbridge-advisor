"""Compare Sydney text retrieval without changing stored embeddings.

Run with the local Titan corpus and Bedrock credentials configured. Candidate vectors
exist only in memory; reports contain rankings and timings, never credentials.
"""

import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.domain.policies.retrieval_query import expand_retrieval_query
from app.infrastructure.bedrock.client import build_bedrock_runtime_client
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.database.models import DocumentChunk
from app.infrastructure.database.session import get_session_factory

MODEL = "cohere.embed-english-v3"
BASELINE = "amazon.titan-embed-text-v2:0"
EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def unit(vector: list[float]) -> list[float]:
    if len(vector) != 1024 or not all(math.isfinite(x) for x in vector):
        raise ValueError("Invalid embedding dimensions or values")
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ValueError("Zero embedding")
    return [x / norm for x in vector]


def main() -> None:
    settings = get_settings()
    if settings.aws_region != "ap-southeast-2":
        raise ValueError("This comparison requires Sydney")
    client = build_bedrock_runtime_client(
        settings.model_copy(update={"bedrock_timeout_seconds": 60.0, "bedrock_max_retries": 1})
    )

    def cohere(texts: list[str], input_type: str) -> list[list[float]]:
        response = client.invoke_model(
            modelId=MODEL,
            body=json.dumps({"texts": texts, "input_type": input_type, "truncate": "END"}),
        )
        data = json.loads(response["body"].read())
        vectors = data["embeddings"]
        if len(vectors) != len(texts):
            raise ValueError("Embedding count mismatch")
        return [unit(vector) for vector in vectors]

    with get_session_factory()() as session:
        chunks = session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.embedding_model == BASELINE)
            .order_by(DocumentChunk.id)
        ).all()
        records = [(str(c.id), c.document_id, c.content, unit(list(c.embedding))) for c in chunks]
    if not records:
        raise ValueError("The local Titan corpus is empty")
    batches = [[r[2] for r in records[i : i + 16]] for i in range(0, len(records), 16)]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        candidate = [
            v for batch in pool.map(lambda b: cohere(b, "search_document"), batches) for v in batch
        ]
    indexing_seconds = round(time.perf_counter() - started, 2)
    titan = BedrockTitanEmbedder(client, BASELINE, 1024)
    questions = json.loads((EVAL_DIR / "questions.json").read_text())["items"]
    rows: list[dict[str, Any]] = []
    for item in questions:
        if item["expected_status"] != "answered":
            continue
        query = expand_retrieval_query(item["question"])
        for model, vectors in [(BASELINE, [r[3] for r in records]), (MODEL, candidate)]:
            started = time.perf_counter()
            q = unit(
                titan.embed_query(query)
                if model == BASELINE
                else cohere([query], "search_query")[0]
            )
            elapsed = round((time.perf_counter() - started) * 1000)
            scores = [sum(a * b for a, b in zip(q, v, strict=True)) for v in vectors]
            indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:6]
            sources = [records[i][1] for i in indices]
            expected = set(item["expected_sources_any"])
            rows.append(
                {
                    "id": item["id"],
                    "model": model,
                    "query_embedding_ms": elapsed,
                    "top_1_source_match": bool(expected.intersection(sources[:1])),
                    "top_3_source_match": bool(expected.intersection(sources[:3])),
                    "top_6_source_match": bool(expected.intersection(sources)),
                    "sources": sources,
                    "similarities": [round(scores[i], 4) for i in indices],
                }
            )
            print(item["id"], model, "top6", rows[-1]["top_6_source_match"], flush=True)
    summary: dict[str, Any] = {
        "region": settings.aws_region,
        "chunks": len(records),
        "documents": len({r[1] for r in records}),
        "candidate_indexing_seconds": indexing_seconds,
        "limits": (
            "Source-selection proxy on the existing small eval set; no answer-quality "
            "or held-out evaluation. No similarity floor applied: models need separate calibration."
        ),
        "models": {},
    }
    for model in (BASELINE, MODEL):
        selected = [r for r in rows if r["model"] == model]
        summary["models"][model] = {
            "questions": len(selected),
            **{
                f"top_{k}_source_matches": sum(r[f"top_{k}_source_match"] for r in selected)
                for k in (1, 3, 6)
            },
            "median_query_embedding_ms": statistics.median(
                r["query_embedding_ms"] for r in selected
            ),
        }
    report = EVAL_DIR / "results" / "sydney-embedding-comparison.json"
    report.write_text(json.dumps({"summary": summary, "results": rows}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
