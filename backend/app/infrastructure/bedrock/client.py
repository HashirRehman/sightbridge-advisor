from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings


def build_bedrock_runtime_client(settings: Settings) -> Any:
    """bedrock-runtime client with bounded timeouts and a modest retry budget."""
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=5,
            read_timeout=settings.bedrock_timeout_seconds,
            retries={"max_attempts": settings.bedrock_max_retries + 1, "mode": "standard"},
        ),
    )
