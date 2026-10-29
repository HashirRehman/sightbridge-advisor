"""Run the RAG eval, saving answers and evidence for review. Scores are computed."""

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.models.answers import DISCLAIMER, AssistantAnswer
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.factory import build_answer_service, build_embedder, build_generator

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


def evaluation_row(
    item: dict[str, Any], answer: AssistantAnswer, latency_ms: int
) -> dict[str, Any]:
    cited = sorted({c.document_id for c in answer.citations})
    status_ok = answer.status.value == item["expected_status"]
    reason_ok = "expected_reason" not in item or (
        answer.reason is not None and answer.reason.value == item["expected_reason"]
    )
    source_ok = None
    if item["expected_status"] == "answered":
        source_ok = bool(set(cited) & set(item["expected_sources_any"]))
    has_disclaimer = answer.disclaimer == DISCLAIMER and answer.render_text().endswith(DISCLAIMER)
    return {
        "id": item["id"],
        "question": item["question"],
        "category": item["category"],
        "expected": item["expected_status"],
        "expected_reason": item.get("expected_reason"),
        "expected_sources_any": item.get("expected_sources_any", []),
        "actual": answer.status.value,
        "reason": answer.reason.value if answer.reason else None,
        "cited_sources": cited,
        "source_ok": source_ok,
        "dropped_claims": answer.dropped_claims,
        "retrieved_passages": answer.retrieved_passages,
        "has_disclaimer": has_disclaimer,
        "latency_ms": latency_ms,
        "model_id": answer.model_id,
        "input_tokens": answer.usage.input_tokens if answer.usage else None,
        "output_tokens": answer.usage.output_tokens if answer.usage else None,
        "tokens": answer.usage.input_tokens + answer.usage.output_tokens if answer.usage else None,
        "explanation_claims": len(answer.explanation),
        "key_consideration_count": len(answer.key_considerations),
        "answer_text": answer.render_text(),
        "answer": asdict(answer),
        "passed": status_ok and reason_ok and source_ok is not False and has_disclaimer,
    }


def isolated_model_settings(settings: Settings, model_id: str) -> Settings:
    return settings.model_copy(
        update={
            "llm_provider": "bedrock",
            "bedrock_chat_model_id": model_id,
            "bedrock_fallback_chat_model_id": "",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=None)
    parser.add_argument("--chat-model", help="Evaluate one Bedrock model with fallback disabled")
    args = parser.parse_args()
    settings = get_settings()
    if args.chat_model:
        settings = isolated_model_settings(settings, args.chat_model)
    configure_logging("WARNING")
    embedder, generator = build_embedder(settings), build_generator(settings)
    label = args.label or re.sub(
        r"[^A-Za-z0-9_.-]", "_", f"{embedder.model_id}__{getattr(generator, 'model_id', 'llm')}"
    )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label) or label in {".", ".."}:
        parser.error("--label must use only letters, digits, underscores, dots or hyphens")
    items = json.loads((EVAL_DIR / "questions.json").read_text())["items"]
    rows = []
    with get_session_factory()() as session:
        service = build_answer_service(session, settings, embedder, generator)
        for item in items:
            started = time.perf_counter()
            answer = service.answer(item["question"])
            row = evaluation_row(item, answer, round((time.perf_counter() - started) * 1000))
            rows.append(row)
            print(
                f"{'PASS' if row['passed'] else 'FAIL'}  {item['id']:24} "
                f"expected={item['expected_status']:11} actual={answer.status.value:11} "
                f"reason={row['reason'] or '-':30} sources={','.join(row['cited_sources']) or '-'}"
            )
    by_category: dict[str, Counter[str]] = {}
    for row in rows:
        by_category.setdefault(row["category"], Counter())[
            "passed" if row["passed"] else "failed"
        ] += 1
    summary = {
        "format_version": 2,
        "label": label,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding_model": embedder.model_id,
        "generator": getattr(generator, "model_id", None) or settings.bedrock_chat_model_id,
        "fallback_model": (
            settings.bedrock_fallback_chat_model_id if settings.llm_provider == "bedrock" else None
        ),
        "responses_by_model": dict(Counter(row["model_id"] for row in rows if row["model_id"])),
        "min_similarity": settings.retrieval_min_similarity,
        "generation_timeout_seconds": settings.generation_timeout_seconds,
        "answer_timeout_seconds": settings.answer_timeout_seconds,
        "max_output_tokens": settings.max_output_tokens,
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "all_have_disclaimer": all(row["has_disclaimer"] for row in rows),
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
    }
    out = EVAL_DIR / "results" / f"{label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": rows}, indent=2, default=str) + "\n")
    print(f"\n{summary['passed']}/{summary['total']} passed")
    for category, counts in summary["by_category"].items():
        print(f"  {category:22} {counts.get('passed', 0)}/{sum(counts.values())}")
    print(f"results → {out.relative_to(EVAL_DIR.parent)}")
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
