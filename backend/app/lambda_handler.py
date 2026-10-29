"""AWS Lambda entry point: API Gateway HTTP API → Mangum → FastAPI (ASGI)."""

import logging
from typing import Any

from mangum import Mangum

from app.main import app

_http_handler = Mangum(app, lifespan="off")


def handler(event: dict[str, Any], context: Any) -> Any:
    if event.get("source") == "sightbridge.answer-worker" and "requestContext" not in event:
        from app.application.chat.worker import process_answer_event

        try:
            return process_answer_event(event)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "worker infrastructure failure", extra={"error_type": type(exc).__name__}
            )
            return {"status": "failed"}
    return _http_handler(event, context)
