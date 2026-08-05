from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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

    llm_provider: Literal["openai", "anthropic", "gemini"]
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    embedding_provider: Literal["openai", "gemini", "local"] = "local"
    embedding_model: str | None = None

    rag_top_k: int = Field(default=3, ge=1)
    rag_min_score: float = Field(default=0.55, ge=0.0, le=1.0)

    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)

    cors_allowed_origins: str = "http://localhost:5173"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level_case(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value

    @field_validator("embedding_model", mode="before")
    @classmethod
    def _blank_embedding_model_is_provider_default(cls, value: str | None) -> str | None:
        return value or None

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

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
