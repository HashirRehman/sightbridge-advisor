"""Operational progress only: no model reasoning, prompts or user text."""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
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
