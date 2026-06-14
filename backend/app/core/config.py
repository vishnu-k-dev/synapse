from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Check both backend/.env and repo-root .env (for alembic run from backend/)
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: Literal["development", "production", "test"] = "development"
    app_debug: bool = False
    log_level: str = "INFO"
    api_key: str = Field(..., description="Bearer token for API authentication")

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "synapse"
    postgres_user: str = "synapse"
    postgres_password: str = Field(..., description="Postgres password")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        """Used by Alembic (sync)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Neo4j ──────────────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = Field(..., description="Neo4j password")

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── MinIO ──────────────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    # Endpoint used to build presigned URLs handed to external clients. When the
    # backend talks to MinIO over a Docker-internal host (minio:9000) but clients
    # reach it at localhost:9000, set this so signatures match the fetch host.
    # Empty → fall back to minio_endpoint.
    minio_public_endpoint: str = ""
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket_specs: str = "synapse-specs"
    minio_bucket_artifacts: str = "synapse-artifacts"
    minio_secure: bool = False
    # Explicit region avoids the SDK's region-discovery HTTP call, which is
    # required for the presign client (its endpoint isn't reachable from here).
    minio_region: str = "us-east-1"

    @property
    def minio_presign_endpoint(self) -> str:
        return self.minio_public_endpoint or self.minio_endpoint

    # ── LLM ───────────────────────────────────────────────────────────────────
    openai_api_key: str = Field(..., description="OpenAI API key")
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    openai_embedding_model: str = "text-embedding-3-small"
    openai_primary_model: str = "gpt-4o-mini"
    anthropic_fallback_model: str = "claude-haiku-4-5-20251001"

    # ── Security ──────────────────────────────────────────────────────────────
    credential_encryption_key: str = Field(
        ..., description="Base64-encoded 32-byte key for AES-256-GCM"
    )

    @field_validator("credential_encryption_key")
    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        key_bytes = base64.b64decode(v)
        if len(key_bytes) != 32:
            raise ValueError("credential_encryption_key must decode to exactly 32 bytes")
        return v

    @property
    def encryption_key_bytes(self) -> bytes:
        return base64.b64decode(self.credential_encryption_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
