"""Application settings.

All configuration comes from environment variables (or a local ``.env`` file). Secrets are
never hard-coded; the only defaults provided are safe local-development values, and
``Settings.validate_for_production`` refuses to start a production deployment with them.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105 - explicit dev sentinel


class LLMProvider(StrEnum):
    SIMULATED = "simulated"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class EmbeddingProvider(StrEnum):
    HASHING = "hashing"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"


class ExecutionMode(StrEnum):
    INPROCESS = "inprocess"
    WORKER = "worker"


class TelemetryBackendKind(StrEnum):
    HTTP = "http"
    INPROCESS = "inprocess"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- runtime ---------------------------------------------------------------------------
    app_env: str = Field(default="local", description="local | test | staging | production")
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104 - container bind address
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # --- storage ---------------------------------------------------------------------------
    database_url: str = "postgresql+psycopg://veriops:veriops@localhost:5432/veriops"
    redis_url: str = "redis://localhost:6379/0"
    use_postgres_checkpointer: bool = True

    # --- execution -------------------------------------------------------------------------
    execution_mode: ExecutionMode = ExecutionMode.INPROCESS
    telemetry_backend: TelemetryBackendKind = TelemetryBackendKind.HTTP
    simulator_url: str = "http://localhost:8100"

    # --- LLM -------------------------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.SIMULATED
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str | None = None
    azure_openai_deployment_small: str | None = None
    anthropic_api_key: SecretStr | None = None
    llm_model_small: str = "gpt-4.1-mini"
    llm_model_medium: str = "gpt-4.1"
    llm_model_large: str = "gpt-4.1"
    llm_timeout_seconds: float = 45.0
    llm_cache_enabled: bool = True

    embedding_provider: EmbeddingProvider = EmbeddingProvider.HASHING
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 384

    # --- bounded loops -----------------------------------------------------------------------
    max_investigation_iterations: int = 5
    max_remediation_attempts: int = 3
    max_tool_retries: int = 3
    max_llm_retries: int = 2
    tool_timeout_seconds: float = 10.0
    approval_timeout_minutes: int = 60

    # --- security ------------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480
    rate_limit_per_minute: int = 120
    seed_demo_users: bool = True
    demo_user_password: SecretStr = SecretStr("veriops-demo")  # local demo only

    # --- observability ---------------------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "veriops-api"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    # --- evaluation ------------------------------------------------------------------------
    eval_min_root_cause_accuracy: float = 0.8
    eval_min_unsafe_rejection_rate: float = 1.0

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_production(self) -> None:
        """Fail fast when insecure development defaults are used in production."""
        problems: list[str] = []
        if self.jwt_secret.get_secret_value() == _DEV_JWT_SECRET:
            problems.append("JWT_SECRET must be set to a strong secret")
        if self.seed_demo_users:
            problems.append("SEED_DEMO_USERS must be false")
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
