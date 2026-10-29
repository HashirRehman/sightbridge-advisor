"""Run the RAG evaluation set against the ingested corpus with the configured providers.

    uv run python -m scripts.run_eval                 # uses EMBEDDING_PROVIDER / LLM_PROVIDER
    uv run python -m scripts.run_eval --label titan-haiku

Writes eval/results/<label>.json and prints a summary. Scores are computed, never edited.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.factory import build_answer_service, build_embedder, build_generator

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    embedder, generator = build_embedder(settings), build_generator(settings)
    label = args.label or f"{embedder.model_id}__{getattr(generator, 'model_id', 'llm')}"
    items = json.loads((EVAL_DIR / "questions.json").read_text())["items"]

    rows = []
    with get_session_factory()() as session:
        service = build_answer_service(session, settings, embedder, generator)
        for item in items:
            started = time.perf_counter()
            answer = service.answer(item["question"])
            cited = sorted({c.document_id for c in answer.citations})
            status_ok = answer.status.value == item["expected_status"]
            reason_ok = "expected_reason" not in item or (
                answer.reason is not None and answer.reason.value == item["expected_reason"]
            )
            source_ok = None
            if item["expected_status"] == "answered":
                source_ok = bool(set(cited) & set(item["expected_sources_any"]))
            passed = status_ok and reason_ok and source_ok is not False
            rows.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "expected": item["expected_status"],
                    "actual": answer.status.value,
                    "reason": answer.reason.value if answer.reason else None,
                    "cited_sources": cited,
                    "source_ok": source_ok,
                    "dropped_claims": answer.dropped_claims,
                    "has_disclaimer": answer.render_text().endswith(answer.disclaimer),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "tokens": (
                        answer.usage.input_tokens + answer.usage.output_tokens
                        if answer.usage
                        else None
                    ),
                    "passed": passed,
                }
            )
            mark = "PASS" if passed else "FAIL"
            print(
                f"{mark}  {item['id']:24} expected={item['expected_status']:11} "
                f"actual={answer.status.value:11} reason={rows[-1]['reason'] or '-':30} "
                f"sources={','.join(cited) or '-'}"
            )

    by_category: dict[str, Counter[str]] = {}
    for r in rows:
        by_category.setdefault(r["category"], Counter())["passed" if r["passed"] else "failed"] += 1
    summary = {
        "label": label,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding_model": embedder.model_id,
        "generator": getattr(generator, "model_id", None) or settings.bedrock_chat_model_id,
        "min_similarity": settings.retrieval_min_similarity,
        "passed": sum(r["passed"] for r in rows),
        "total": len(rows),
        "all_have_disclaimer": all(r["has_disclaimer"] for r in rows),
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
    }
    out = EVAL_DIR / "results" / f"{label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": rows}, indent=2) + "\n")

    print(
        f"\n{summary['passed']}/{summary['total']} passed  "
        f"(disclaimer on every answer: {summary['all_have_disclaimer']})"
    )
    for category, counts in summary["by_category"].items():
        print(f"  {category:22} {counts.get('passed', 0)}/{sum(counts.values())}")
    print(f"results → {out.relative_to(EVAL_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
