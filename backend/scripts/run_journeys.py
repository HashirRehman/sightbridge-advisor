"""Replay multi-turn reader journeys through ChatService and save full transcripts.

Each journey is one thread, so history, evidence reuse and ownership behave exactly as in
the browser. Every model call is recorded with tokens and an indicative cost. Checks are
minimal guards; the markdown transcript is the artifact to read.

    uv run python -m scripts.run_journeys --label after-refactor [--only beginner,reported]
"""

import argparse
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.application.chat.service import ChatService
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.models.answers import DISCLAIMER
from app.domain.models.identity import Principal
from app.infrastructure.database.models import Exchange
from app.infrastructure.database.session import get_session_factory
from app.infrastructure.factory import build_answer_service, build_embedder, build_generator

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
_MODEL_LABELS = {"deepseek.v3.2": "DeepSeek", "amazon.titan-embed-text-v2:0": "Titan"}


def turn_checks(turn: dict[str, Any], exchange: Exchange, previous: str | None) -> list[str]:
    """Return failed guard descriptions; an empty list means the turn passed."""
    failures = []
    body = exchange.answer_text.replace(DISCLAIMER, "").strip()
    if exchange.status not in turn["expect"]:
        failures.append(f"status {exchange.status}/{exchange.reason} not in {turn['expect']}")
    if turn.get("cites") and not exchange.sources:
        failures.append("expected citations")
    for word in turn.get("mentions", []):
        if word.casefold() not in body.casefold():
            failures.append(f"missing {word!r}")
    # Refusal and unsupported notices are fixed text; only generated answers are checked.
    generated = body if exchange.status == "answered" else ""
    for word in turn.get("not_mentions", []):
        if re.search(rf"\b{re.escape(word)}\b", generated):
            failures.append(f"unexpected {word!r}")
    if turn.get("differs_from_previous") and previous is not None:
        ratio = SequenceMatcher(None, body.casefold(), previous.casefold()).ratio()
        if ratio >= 0.6:
            failures.append(f"near-copy of previous answer (similarity {ratio:.2f})")
    return failures


def summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    llm = [c for c in calls if c.get("operation") == "converse"]
    return {
        "llm_calls": len(llm),
        "embedding_calls": len(calls) - len(llm),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
        "estimated_cost_usd": round(sum(c.get("estimated_cost_usd") or 0 for c in calls), 6),
        "failed_calls": sum(c.get("status") != "completed" for c in calls),
    }


def model_label(model_id: str) -> str:
    return _MODEL_LABELS.get(model_id, "Nova Lite" if "nova-lite" in model_id else model_id)


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["summary"]
    lines = [
        f"# Journey run `{report['label']}`",
        "",
        f"{totals['passed_turns']}/{totals['turns']} turns passed their guards. "
        f"{totals['llm_calls']} LLM calls, {totals['embedding_calls']} embedding calls, "
        f"{totals['seconds']:.0f}s, about ${totals['estimated_cost_usd']:.4f}.",
        "",
    ]
    for journey in report["journeys"]:
        lines += [f"## {journey['id']} — {journey['reader']}", ""]
        for turn in journey["turns"]:
            usage = turn["usage"]
            mark = "PASS" if not turn["failures"] else "FAIL: " + "; ".join(turn["failures"])
            sequence = " → ".join(model_label(c["model_id"]) for c in turn["calls"]) or "no model"
            lines += [
                f"**User:** {turn['question']}",
                "",
                f"*{turn['status']}/{turn['reason'] or '-'} · {turn['seconds']:.1f}s · "
                f"{usage['llm_calls']} LLM + {usage['embedding_calls']} embedding "
                f"({sequence}) · {mark}*",
                "",
                "> " + turn["answer"].replace("\n", "\n> "),
                "",
            ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--only", default="", help="Comma-separated journey IDs")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.label):
        parser.error("--label must use only letters, digits, underscores, dots or hyphens")
    configure_logging("WARNING")
    settings = get_settings()
    selected = {name for name in args.only.split(",") if name}
    journeys = [
        j
        for j in json.loads((EVAL_DIR / "journeys.json").read_text())["journeys"]
        if not selected or j["id"] in selected
    ]
    embedder = build_embedder(settings)
    generator = build_generator(
        settings,
        primary_timeout=settings.worker_generation_timeout_seconds,
        fallback_timeout=settings.worker_answer_timeout_seconds,
    )
    principal = Principal(f"journey-{uuid.uuid4()}", "journeys@example.invalid", False)
    results = []
    with get_session_factory()() as session:
        answers = build_answer_service(
            session,
            settings,
            embedder,
            generator,
            timeout_seconds=settings.worker_answer_timeout_seconds,
        )
        chat = ChatService(session, answers, per_minute=1000, per_hour=10000, max_concurrent=20)
        for journey in journeys:
            conversation_id = None
            previous: str | None = None
            turns = []
            for turn in journey["turns"]:
                exchange = chat.ask(principal, turn["question"], conversation_id, None)
                conversation_id = exchange.conversation_id
                calls = exchange.answer_payload.get("model_usage", [])
                failures = turn_checks(turn, exchange, previous)
                previous = exchange.answer_text.replace(DISCLAIMER, "").strip()
                turns.append(
                    {
                        "question": turn["question"],
                        "status": exchange.status,
                        "reason": exchange.reason,
                        "answer": exchange.answer_text,
                        "sources": [s["document_id"] for s in exchange.sources],
                        "seconds": exchange.latency_ms / 1000,
                        "calls": calls,
                        "activity": [s["kind"] for s in exchange.answer_payload["activity"]],
                        "usage": summarize_calls(calls),
                        "failures": failures,
                    }
                )
                print(
                    f"{'PASS' if not failures else 'FAIL'}  {journey['id']:12} "
                    f"{turns[-1]['usage']['llm_calls']} LLM {turns[-1]['seconds']:5.1f}s  "
                    f"{turn['question'][:60]}" + (f"  ← {'; '.join(failures)}" if failures else "")
                )
            results.append({"id": journey["id"], "reader": journey["reader"], "turns": turns})
    every_turn = [t for j in results for t in j["turns"]]
    summary = {
        "turns": len(every_turn),
        "passed_turns": sum(not t["failures"] for t in every_turn),
        "seconds": sum(t["seconds"] for t in every_turn),
        **{
            key: round(sum(t["usage"][key] for t in every_turn), 6)
            for key in (
                "llm_calls",
                "embedding_calls",
                "input_tokens",
                "output_tokens",
                "estimated_cost_usd",
                "failed_calls",
            )
        },
    }
    report = {
        "label": args.label,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chat_model": settings.bedrock_chat_model_id,
        "fallback_model": settings.bedrock_fallback_chat_model_id,
        "review_model": settings.review_model_id,
        "min_similarity": settings.retrieval_min_similarity,
        "summary": summary,
        "journeys": results,
    }
    out = EVAL_DIR / "results" / f"journeys-{args.label}"
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    out.with_suffix(".md").write_text(render_markdown(report))
    print(
        f"\n{summary['passed_turns']}/{summary['turns']} turns passed · "
        f"{summary['llm_calls']} LLM calls · {summary['seconds']:.0f}s · "
        f"${summary['estimated_cost_usd']:.4f}\nresults → {out.relative_to(EVAL_DIR.parent)}.md"
    )
    return 0 if summary["passed_turns"] == summary["turns"] else 1


if __name__ == "__main__":
    sys.exit(main())
