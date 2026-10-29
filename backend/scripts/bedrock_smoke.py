"""Prove the configured Bedrock models can actually be invoked (listing them is not enough).

    AWS_PROFILE=<profile> uv run python -m scripts.bedrock_smoke

Costs well under $0.01. Exit code is non-zero if any model fails.
"""

import json
import sys
import time

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.infrastructure.bedrock.client import build_bedrock_runtime_client


def main() -> int:
    settings = get_settings()
    runtime = build_bedrock_runtime_client(settings)
    failures = 0
    chat_models = [settings.bedrock_chat_model_id, settings.bedrock_fallback_chat_model_id]
    for model in filter(None, chat_models):
        started = time.perf_counter()
        try:
            runtime.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": "Reply with: ok"}]}],
                inferenceConfig={"maxTokens": 5, "temperature": 0},
            )
            print(f"OK    {model}  {round((time.perf_counter() - started) * 1000)} ms")
        except ClientError as exc:
            failures += 1
            error = exc.response["Error"]
            print(f"FAIL  {model}  {error['Code']}: {error['Message']}")
    try:
        body = runtime.invoke_model(
            modelId=settings.bedrock_embedding_model_id,
            body=json.dumps({"inputText": "1031 exchange", "dimensions": 1024, "normalize": True}),
        )["body"].read()
        dims = len(json.loads(body)["embedding"])
        print(f"OK    {settings.bedrock_embedding_model_id}  dims={dims}")
    except ClientError as exc:
        failures += 1
        print(f"FAIL  {settings.bedrock_embedding_model_id}  {exc.response['Error']['Message']}")
    if failures:
        print("\nCheck the individual model errors above before proceeding.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
