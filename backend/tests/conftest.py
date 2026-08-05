import logging

import pytest

from app.core.config import Settings

# Every env var Settings reads. Cleared per-test so a developer's real shell
# environment (or a stray CI variable) can never change a test's outcome.
_SETTINGS_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level


def make_settings(**overrides) -> Settings:
    """Settings built from explicit values only — never from backend/.env."""
    values = {
        "jwt_secret_key": "test-secret",
        "llm_provider": "openai",
        "cors_allowed_origins": "http://localhost:5173",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()
