from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value comes from the environment (or `.env` locally)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sightbridge:sightbridge@localhost:5432/sightbridge"
    )

    # Comma-separated list; the browser never calls the API directly in production
    # (the Next.js server proxies), so this stays narrow.
    cors_allowed_origins: str = "http://localhost:3000"

    aws_region: str = "us-east-1"
    # "local" = extractive stand-in for dev without AWS; refused in production.
    llm_provider: Literal["bedrock", "local"] = "bedrock"
    bedrock_chat_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # "local" = deterministic lexical embedder for dev/tests without AWS; refused in production.
    embedding_provider: Literal["bedrock", "local"] = "bedrock"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    bedrock_timeout_seconds: float = 20.0
    bedrock_max_retries: int = 2
    max_output_tokens: int = 900

    # "local" = HS256 tokens minted by the Next.js dev server; refused in production.
    auth_provider: Literal["cognito", "local"] = "cognito"
    local_auth_secret: SecretStr = SecretStr("")
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    admin_group_name: str = "admin"

    documents_bucket: str = ""

    # Retrieval and policy knobs (calibrated against eval/ — see docs/decisions.md).
    retrieval_top_k: int = Field(default=6, ge=1, le=12)
    retrieval_min_similarity: float = Field(default=0.30, ge=0.0, le=1.0)
    max_question_chars: int = Field(default=1000, ge=50, le=4000)

    rate_limit_per_minute: int = 6
    rate_limit_per_hour: int = 60

    @model_validator(mode="after")
    def production_guards(self) -> "Settings":
        if self.environment == "production" and (
            self.embedding_provider != "bedrock" or self.llm_provider != "bedrock"
        ):
            raise ValueError("production must use Bedrock for embeddings and generation")
        if self.environment == "production" and (
            self.auth_provider != "cognito"
            or not self.cognito_user_pool_id
            or not self.cognito_app_client_id
        ):
            raise ValueError("production requires Cognito auth with pool and client IDs")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def cognito_issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
