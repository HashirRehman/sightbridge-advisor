"""A shared wall-clock budget for model calls within one answer."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("answer_deadline", default=None)


@contextmanager
def answer_budget(seconds: float) -> Iterator[None]:
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds(limit: float) -> float:
    deadline = _deadline.get()
    return limit if deadline is None else max(0.0, min(limit, deadline - time.monotonic()))
