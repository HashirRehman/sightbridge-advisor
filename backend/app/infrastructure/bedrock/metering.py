"""SDK boundary metering, including routing, validation, retries and embeddings.

No prompts, credentials or response bodies enter telemetry. Unknown usage remains null;
a timed-out thread still running at publication must never be counted as a free call.
"""

import io
import json
import time
from typing import Any

from botocore.response import StreamingBody

from app.application.rag.activity import begin_model_call, finish_model_call


class MeteredBedrockClient:
    def __init__(self, client: Any, region: str):
        self._client = client
        self._region = region

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def converse(self, **kwargs: Any) -> Any:
        return self._invoke("converse", kwargs)

    def invoke_model(self, **kwargs: Any) -> Any:
        return self._invoke("invoke_model", kwargs)

    def _invoke(self, operation: str, kwargs: dict[str, Any]) -> Any:
        handle = begin_model_call(str(kwargs.get("modelId", "unknown")), operation, self._region)
        started = time.monotonic()
        try:
            response = getattr(self._client, operation)(**kwargs)
            input_tokens = output_tokens = None
            if operation == "converse":
                usage = response.get("usage", {})
                input_tokens = usage.get("inputTokens")
                output_tokens = usage.get("outputTokens")
            elif "titan-embed" in str(kwargs.get("modelId", "")):
                # Preserve the stream contract for LangChain after inspecting token counts.
                raw = response["body"].read()
                response["body"] = StreamingBody(io.BytesIO(raw), len(raw))
                try:
                    input_tokens = json.loads(raw).get("inputTextTokenCount")
                    output_tokens = 0  # Embeddings do not generate text tokens.
                except (ValueError, AttributeError):
                    pass
            finish_model_call(
                handle,
                status="completed",
                elapsed_ms=round((time.monotonic() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return response
        except Exception:
            finish_model_call(
                handle, status="failed", elapsed_ms=round((time.monotonic() - started) * 1000)
            )
            raise
