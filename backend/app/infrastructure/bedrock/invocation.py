"""Bound caller wait time, including SDK retries, queueing and response parsing.

Only model I/O runs on workers; database sessions stay on the request thread. Python cannot
cancel an in-flight HTTP request: a timed-out call may finish (and be billed) later. Its
result is discarded. Socket timeouts and disabled default retries limit that extra work.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from app.application.rag.budget import remaining_seconds

_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bedrock")


def invoke_bounded[T](operation: Callable[[], T], timeout_seconds: float) -> T:
    timeout = remaining_seconds(timeout_seconds)
    if timeout <= 0:
        raise TimeoutError("answer time budget exhausted")
    future = _workers.submit(copy_context().run, operation)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()  # cancels queued work; running HTTP calls cannot be cancelled
        raise
