from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

from .conftest import make_settings

ENV_FILE_CONTENT = """\
APP_ENV=dev
LOG_LEVEL=INFO
DATABASE_URL=sqlite:///./app.db
SEED_DATA_DIR=./seed_data
SEED_ON_STARTUP=true
JWT_SECRET_KEY=dev-only-change-me
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60
SESSION_COOKIE_NAME=session
SESSION_COOKIE_SECURE=false
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-5
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
EMBEDDING_PROVIDER=local
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
GEMINI_EMBEDDING_MODEL=text-embedding-004
LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RAG_TOP_K=3
RAG_MIN_SCORE=0.55
DEFAULT_PAGE_SIZE=20
MAX_PAGE_SIZE=100
CORS_ALLOWED_ORIGINS=http://localhost:5173
"""


def test_loads_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_FILE_CONTENT, encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "dev"
    assert settings.log_level == "INFO"
    assert settings.database_url == "sqlite:///./app.db"
    assert settings.seed_data_dir == "./seed_data"
    assert settings.seed_on_startup is True
    assert settings.jwt_secret_key == "dev-only-change-me"
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_expire_minutes == 60
    assert settings.session_cookie_name == "session"
    assert settings.session_cookie_secure is False
    assert settings.llm_provider == "openai"
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.anthropic_model == "claude-sonnet-4-5"
    assert settings.gemini_model == "gemini-2.5-flash"
    assert settings.embedding_provider == "local"
    assert settings.openai_embedding_model == "text-embedding-3-small"
    assert settings.gemini_embedding_model == "text-embedding-004"
    assert settings.local_embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.rag_top_k == 3
    assert settings.rag_min_score == 0.55
    assert settings.default_page_size == 20
    assert settings.max_page_size == 100
    assert settings.cors_origins == ["http://localhost:5173"]


def test_env_example_is_a_valid_settings_source():
    """.env.example is the file the README tells a developer to copy to .env."""
    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert example.is_file(), ".env.example is missing"

    settings = Settings(_env_file=example)

    assert settings.jwt_secret_key
    assert settings.llm_provider in {"openai", "anthropic", "gemini"}
    assert settings.cors_origins


def test_defaults_match_readme_when_only_required_vars_are_set():
    settings = Settings(
        _env_file=None, jwt_secret_key="s", llm_provider="anthropic"
    )

    assert settings.app_env == "dev"
    assert settings.log_level == "INFO"
    assert settings.database_url == "sqlite:///./app.db"
    assert settings.seed_data_dir == "./seed_data"
    assert settings.seed_on_startup is True
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_expire_minutes == 60
    assert settings.session_cookie_name == "session"
    assert settings.session_cookie_secure is True
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.anthropic_model == "claude-sonnet-4-5"
    assert settings.gemini_model == "gemini-2.5-flash"
    assert settings.embedding_provider == "local"
    assert settings.local_embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    # LLM_PROVIDER=anthropic has no bearing on embedding_model — Anthropic has
    # no embeddings API, so RAG must still resolve a usable model here.
    assert settings.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.rag_top_k == 3
    assert settings.rag_min_score == 0.55
    assert settings.default_page_size == 20
    assert settings.max_page_size == 100


@pytest.mark.parametrize("missing", ["jwt_secret_key", "llm_provider"])
def test_required_fields_are_required(missing):
    values = {"jwt_secret_key": "s", "llm_provider": "openai"}
    del values[missing]

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **values)

    assert missing in str(exc_info.value)


def test_reads_env_vars_case_insensitively(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "from-environ")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://admin.example.test")

    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key == "from-environ"
    assert settings.llm_provider == "gemini"
    assert settings.jwt_expire_minutes == 15
    assert settings.cors_origins == ["http://admin.example.test"]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_llm_provider_accepts_the_three_documented_providers(provider):
    assert make_settings(llm_provider=provider).llm_provider == provider


def test_llm_provider_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        make_settings(llm_provider="llama")


def test_app_env_rejects_unknown_value():
    with pytest.raises(ValidationError):
        make_settings(app_env="staging")


# --- CORS_ALLOWED_ORIGINS (B1 acceptance criterion) ---------------------------


@pytest.mark.parametrize(
    "value",
    [
        "*",
        " * ",
        "http://localhost:5173,*",
        "*,http://localhost:5173",
        "http://a.test, * ,http://b.test",
    ],
    ids=["bare", "padded", "trailing", "leading", "middle"],
)
def test_wildcard_cors_origin_is_rejected(value):
    with pytest.raises(ValidationError) as exc_info:
        make_settings(cors_allowed_origins=value)

    assert "CORS_ALLOWED_ORIGINS" in str(exc_info.value)


@pytest.mark.parametrize(
    "value", ["", "   ", ",", " , ,, "], ids=["empty", "blank", "comma", "commas"]
)
def test_blank_cors_origins_is_rejected(value):
    with pytest.raises(ValidationError) as exc_info:
        make_settings(cors_allowed_origins=value)

    assert "at least one origin" in str(exc_info.value)


def test_cors_origins_parses_comma_separated_list():
    settings = make_settings(
        cors_allowed_origins="http://localhost:5173,https://admin.example.test"
    )

    assert settings.cors_origins == [
        "http://localhost:5173",
        "https://admin.example.test",
    ]


def test_cors_origins_strips_whitespace_and_empty_entries():
    settings = make_settings(
        cors_allowed_origins="  http://a.test ,, https://b.test  ,  "
    )

    assert settings.cors_origins == ["http://a.test", "https://b.test"]


def test_cors_origins_single_value():
    assert make_settings(cors_allowed_origins="http://a.test").cors_origins == [
        "http://a.test"
    ]


# --- derived properties -------------------------------------------------------


def test_docs_enabled_in_dev():
    assert make_settings(app_env="dev").docs_enabled is True


def test_docs_disabled_in_prod():
    assert make_settings(app_env="prod").docs_enabled is False


# --- embedding_model follows embedding_provider, never llm_provider --------


def test_embedding_model_follows_openai_provider():
    settings = make_settings(
        embedding_provider="openai", openai_embedding_model="custom-openai-embed"
    )
    assert settings.embedding_model == "custom-openai-embed"


def test_embedding_model_follows_gemini_provider():
    settings = make_settings(
        embedding_provider="gemini", gemini_embedding_model="custom-gemini-embed"
    )
    assert settings.embedding_model == "custom-gemini-embed"


def test_embedding_model_follows_local_provider():
    settings = make_settings(
        embedding_provider="local", local_embedding_model="custom-local-embed"
    )
    assert settings.embedding_model == "custom-local-embed"


@pytest.mark.parametrize("llm_provider", ["openai", "anthropic", "gemini"])
def test_embedding_model_ignores_llm_provider(llm_provider):
    """The regression this whole design exists to prevent: embedding_model
    must not depend on llm_provider, since Anthropic has no embeddings API."""
    settings = make_settings(
        llm_provider=llm_provider,
        embedding_provider="local",
        local_embedding_model="always-this-one",
    )
    assert settings.embedding_model == "always-this-one"


def test_embedding_provider_rejects_unknown_value():
    with pytest.raises(ValidationError):
        make_settings(embedding_provider="anthropic")


@pytest.mark.parametrize(
    "field,value",
    [
        ("rag_top_k", 0),
        ("rag_min_score", -0.1),
        ("rag_min_score", 1.1),
        ("default_page_size", 0),
        ("max_page_size", 0),
    ],
)
def test_out_of_range_numeric_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


# --- log_level (typo must fail validation, not crash logging.setLevel at boot) ---


@pytest.mark.parametrize(
    "level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
)
def test_log_level_accepts_documented_levels(level):
    assert make_settings(log_level=level).log_level == level


def test_log_level_is_case_insensitive():
    assert make_settings(log_level="debug").log_level == "DEBUG"


def test_log_level_rejects_typo():
    with pytest.raises(ValidationError) as exc_info:
        make_settings(log_level="verbose")

    assert "log_level" in str(exc_info.value)


# --- default_page_size must not exceed max_page_size --------------------------


def test_default_page_size_within_max_is_accepted():
    settings = make_settings(default_page_size=50, max_page_size=100)

    assert settings.default_page_size == 50
    assert settings.max_page_size == 100


def test_default_page_size_equal_to_max_is_accepted():
    settings = make_settings(default_page_size=100, max_page_size=100)

    assert settings.default_page_size == settings.max_page_size == 100


def test_default_page_size_over_max_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        make_settings(default_page_size=200, max_page_size=100)

    message = str(exc_info.value)
    assert "DEFAULT_PAGE_SIZE" in message
    assert "MAX_PAGE_SIZE" in message


# --- jwt_secret_key strength is only enforced in prod --------------------------


def test_short_jwt_secret_key_is_accepted_in_dev():
    settings = make_settings(app_env="dev", jwt_secret_key="dev-only-change-me")
    assert settings.jwt_secret_key == "dev-only-change-me"


def test_short_jwt_secret_key_is_rejected_in_prod():
    with pytest.raises(ValidationError) as exc_info:
        make_settings(app_env="prod", jwt_secret_key="too-short")

    assert "JWT_SECRET_KEY" in str(exc_info.value)


def test_jwt_secret_key_at_the_minimum_length_is_accepted_in_prod():
    key = "a" * 32
    settings = make_settings(app_env="prod", jwt_secret_key=key)
    assert settings.jwt_secret_key == key


def test_jwt_secret_key_one_below_the_minimum_is_rejected_in_prod():
    with pytest.raises(ValidationError):
        make_settings(app_env="prod", jwt_secret_key="a" * 31)


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "cached-secret")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
