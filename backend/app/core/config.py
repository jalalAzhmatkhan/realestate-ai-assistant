from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# PyJWT itself warns (InsecureKeyLengthWarning) below 32 bytes for HS256 — a key
# shorter than its output size doesn't add the entropy the algorithm assumes.
JWT_SECRET_KEY_MIN_LENGTH_PROD = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["dev", "prod"] = "dev"
    log_level: LogLevel = "INFO"

    database_url: str = "sqlite:///./app.db"
    seed_data_dir: str = "./seed_data"
    seed_on_startup: bool = True

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    session_cookie_name: str = "session"
    session_cookie_secure: bool = True

    # Backs the POST /auth/logout token-revocation denylist (app/core/revocation.py).
    # docker-compose's `backend` service overrides this to redis://redis:6379/0 to
    # reach the `redis` service; this default targets a Redis reachable at
    # localhost:6379 for non-Docker local dev. Unlike DATABASE_URL, the app does not
    # hard-fail without a reachable Redis: the denylist client connects lazily and
    # fails OPEN on a revocation check (see revocation.py's module docstring), so
    # `uv run uvicorn` boots fine with no Redis running at all.
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: Literal["openai", "anthropic", "gemini"]
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # embedding_provider is deliberately independent of llm_provider (Anthropic
    # has no embeddings API) — each provider gets its own model var, mirroring
    # openai_model/anthropic_model/gemini_model above, and `embedding_model`
    # below picks the one matching embedding_provider.
    embedding_provider: Literal["openai", "gemini", "local"] = "local"
    openai_embedding_model: str = "text-embedding-3-small"
    gemini_embedding_model: str = "text-embedding-004"
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    rag_top_k: int = Field(default=3, ge=1)
    rag_min_score: float = Field(default=0.55, ge=0.0, le=1.0)

    # Runs the idempotent `app.rag.reindex` routine from the app lifespan (B32/B33),
    # immediately after seed_if_empty. No-op and no embedding calls when the index is
    # already current (hash comparison only). Read here ahead of the reindex command
    # itself landing (B31 is infra-only) so the setting exists before it has a
    # consumer — see Documentation/audits/2026-08-06-pgvector-migration-contract.md.
    rag_index_on_startup: bool = True

    # RAG observability. Both switches are off-by-absence-of-rows, never zeros: a
    # disabled check writes nothing, so the admin page shows an empty state rather
    # than a score that looks like a failing one.
    retrieval_log_enabled: bool = True
    faithfulness_check_enabled: bool = True
    faithfulness_max_concurrent: int = Field(default=4, ge=1)
    faithfulness_max_claims: int = Field(default=20, ge=1)
    eval_set_max_cases: int = Field(default=60, ge=1)

    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)

    cors_allowed_origins: str = "http://localhost:5173"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level_case(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if "*" in origins:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must be an explicit comma-separated origin list, not '*'. "
                "Browser logins (client_type=browser) authenticate with a credentialed httpOnly "
                "cookie, and browsers reject 'Access-Control-Allow-Origin: *' on credentialed "
                "requests — a wildcard here silently breaks admin SPA login rather than loosening it."
            )
        if not origins:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must list at least one origin (e.g. http://localhost:5173)."
            )
        return ",".join(origins)

    @model_validator(mode="after")
    def _default_page_size_within_max(self) -> "Settings":
        if self.default_page_size > self.max_page_size:
            raise ValueError(
                f"DEFAULT_PAGE_SIZE ({self.default_page_size}) must not exceed "
                f"MAX_PAGE_SIZE ({self.max_page_size})."
            )
        return self

    @model_validator(mode="after")
    def _jwt_secret_key_strong_enough_in_prod(self) -> "Settings":
        # Only enforced in prod: a short, memorable secret (e.g. .env.example's
        # "dev-only-change-me") is fine for local dev, where the cost of a weak
        # key is nil. A prod deployment silently running on one is a real gap —
        # this is the one place that combination was previously unvalidated.
        if self.app_env == "prod" and len(self.jwt_secret_key) < JWT_SECRET_KEY_MIN_LENGTH_PROD:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {JWT_SECRET_KEY_MIN_LENGTH_PROD} "
                f"characters when APP_ENV=prod (see README: `openssl rand -hex 32`). "
                f"Shorter values are only accepted in APP_ENV=dev."
            )
        return self

    @property
    def embedding_model(self) -> str:
        """The embedding model for the active embedding_provider — never llm_provider.

        RAG must work regardless of which LLM_PROVIDER is chosen (Anthropic has
        no embeddings API), so this follows embedding_provider exclusively.
        """
        return {
            "openai": self.openai_embedding_model,
            "gemini": self.gemini_embedding_model,
            "local": self.local_embedding_model,
        }[self.embedding_provider]

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
