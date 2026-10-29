"""Operational progress only: no model reasoning, prompts or user text."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Literal

StepKind = Literal[
    "policy",
    "safety",
    "embedding",
    "retrieval",
    "generation",
    "model_call",
    "verification",
    "output_check",
    "privacy_check",
    "understanding",
    "search_refinement",
]
StepStatus = Literal["running", "completed", "failed"]


@dataclass
class ActivityStep:
    id: int
    kind: StepKind
    status: StepStatus
    started_ms: int
    finished_ms: int | None = None
    model_id: str | None = None
    source_count: int | None = None


@dataclass
class ActivityTracker:
    sink: Callable[[list[dict[str, Any]]], None]
    started: float = field(default_factory=time.monotonic)
    steps: list[ActivityStep] = field(default_factory=list)

    model_calls: list[dict[str, Any]] = field(default_factory=list)
    usage_lock: Lock = field(default_factory=Lock, repr=False)

    def usage_snapshot(self) -> list[dict[str, Any]]:
        from app.infrastructure.bedrock.pricing import PRICE_REFERENCE, estimated_cost_usd

        with self.usage_lock:
            calls = [dict(call) for call in self.model_calls]
        for call in calls:
            call["estimated_cost_usd"] = estimated_cost_usd(call)
            call["price_reference"] = PRICE_REFERENCE
        return calls

    def snapshot(self) -> list[dict[str, Any]]:
        return [asdict(step) for step in self.steps]

    def publish(self) -> None:
        try:
            self.sink(self.snapshot())
        except Exception:
            # A telemetry failure must never bypass safety or corrupt an answer.
            logging.getLogger(__name__).warning("activity update unavailable")

    def elapsed(self) -> int:
        return max(0, round((time.monotonic() - self.started) * 1000))


_tracker: ContextVar[ActivityTracker | None] = ContextVar("activity_tracker", default=None)


@contextmanager
def track_activity(tracker: ActivityTracker) -> Iterator[None]:
    token = _tracker.set(tracker)
    try:
        yield
    finally:
        _tracker.reset(token)


@contextmanager
def activity_step(kind: StepKind, model_id: str | None = None) -> Iterator[ActivityStep]:
    tracker = _tracker.get()
    step = ActivityStep(
        len(tracker.steps) if tracker else 0,
        kind,
        "running",
        tracker.elapsed() if tracker else 0,
        model_id=model_id,
    )
    if tracker:
        tracker.steps.append(step)
        tracker.publish()
    try:
        yield step
    except Exception:
        step.status = "failed"
        raise
    else:
        step.status = "completed"
    finally:
        if tracker:
            step.finished_ms = tracker.elapsed()
            tracker.publish()


def begin_model_call(
    model_id: str, operation: str, region: str
) -> tuple[ActivityTracker | None, int]:
    tracker = _tracker.get()
    if tracker is None:
        return None, -1
    with tracker.usage_lock:
        index = len(tracker.model_calls)
        tracker.model_calls.append(
            {
                "model_id": model_id,
                "operation": operation,
                "region": region,
                "status": "running",
                "input_tokens": None,
                "output_tokens": None,
                "latency_ms": None,
            }
        )
    return tracker, index


def finish_model_call(
    handle: tuple[ActivityTracker | None, int],
    *,
    status: str,
    elapsed_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    tracker, index = handle
    if tracker is not None:
        with tracker.usage_lock:
            tracker.model_calls[index].update(
                status=status,
                latency_ms=elapsed_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
