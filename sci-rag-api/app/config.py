"""Central application settings loaded from environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: Literal["development", "staging", "production"] = "production"
    app_name: str = "sci-rag-api"
    app_version: str = "0.1.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_workers: int = 4
    app_log_level: str = "INFO"
    app_debug_responses: bool = False

    # ---- Auth ----
    sci_api_key: str = ""
    webhook_secret: str = "change-me-32bytes-min"
    webhook_sci_url: str = ""

    # ---- Postgres ----
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "rag"
    postgres_user: str = "rag"
    postgres_password: str = "change-me"

    # ---- Redis ----
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # ---- Qdrant ----
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_api_key: str = "change-me"
    qdrant_collection: str = "sci_faq_ecd_ecf"

    # ---- Cloudflare R2 ----
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "rag-images"
    r2_public_base_url: str = ""
    r2_presigned_url_ttl_seconds: int = 3600

    # ---- MinIO (dev fallback) ----
    minio_endpoint: str = "http://minio:9000"
    minio_user: str = "minio"
    minio_pass: str = "minio12345"
    minio_bucket: str = "rag-images-dev"

    # ---- LLM providers ----
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # ---- Embeddings ----
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: Literal["cpu", "cuda", "mps"] = "cpu"
    embedding_batch_size: int = 8
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- RAG thresholds ----
    min_score_top_chunk: float = 0.65
    min_confianca_resposta: float = 0.70
    grey_zone_low: float = 0.65
    grey_zone_high: float = 0.80
    retrieval_top_k: int = 20
    rerank_top_k: int = 5
    query_rewrite_variants: int = 3

    # ---- Cache TTL ----
    cache_ttl_high: int = 86400
    cache_ttl_medium: int = 21600

    # ---- Rate limiting ----
    rate_limit_query: str = "60/minute"
    rate_limit_ingest: str = "5/minute"
    rate_limit_feedback: str = "120/minute"

    # ---- Celery ----
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ---- Observability ----
    prometheus_enabled: bool = True
    grafana_password: str = "admin"

    # ---- Derived ----
    @computed_field  # type: ignore[misc]
    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def postgres_sync_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[misc]
    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @computed_field  # type: ignore[misc]
    @property
    def r2_endpoint_url(self) -> str:
        if not self.r2_account_id:
            return ""
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @computed_field  # type: ignore[misc]
    @property
    def use_r2(self) -> bool:
        return bool(self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton accessor for application settings."""
    return Settings()
