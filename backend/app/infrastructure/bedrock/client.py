import os
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import Settings


def resolve_bedrock_api_key(settings: Settings, ssm_client: Any = None) -> str:
    if not settings.bedrock_api_key_ssm_parameter:
        return settings.aws_bearer_token_bedrock.get_secret_value()
    ssm = ssm_client or boto3.client("ssm", region_name=settings.aws_region)
    response = ssm.get_parameter(Name=settings.bedrock_api_key_ssm_parameter, WithDecryption=True)
    token = str(response["Parameter"]["Value"])
    if not token:
        raise ValueError("Bedrock API key parameter is empty")
    return token


def build_bedrock_runtime_client(settings: Settings) -> Any:
    """bedrock-runtime client with bounded timeouts and a modest retry budget."""
    token = resolve_bedrock_api_key(settings)
    if token:
        # Supported SDK token provider; applies only to Bedrock, not S3/SSM/Cognito.
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=1,
            read_timeout=settings.bedrock_timeout_seconds,
            retries={"total_max_attempts": settings.bedrock_max_retries + 1, "mode": "standard"},
        ),
    )
