import logging
from pathlib import Path

import pytest

from app.core.config import Settings

SEED_DATA_DIR = str(Path(__file__).resolve().parents[1] / "seed_data")
SEED_PASSWORD = "ChangeMe123!"

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
    """Settings built from explicit values only — never from backend/.env.

    jwt_secret_key defaults to 32+ chars so callers testing app_env="prod"
    (e.g. docs_enabled, exception-envelope tests) don't also need to override
    it just to clear the prod-only length check — that check gets its own
    dedicated short-vs-long tests in test_config.py.
    """
    values = {
        "jwt_secret_key": "test-secret-at-least-32-characters-long",
        "llm_provider": "openai",
        "cors_allowed_origins": "http://localhost:5173",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def make_db_settings(tmp_path, **overrides) -> Settings:
    """Settings bound to a throwaway SQLite file, never the shared ./app.db.

    `session_cookie_secure=False` because TestClient talks plain http and a
    `Secure` cookie would not survive the client jar; the `Secure` attribute
    itself is asserted separately in test_auth.py.
    """
    values = {
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "seed_data_dir": SEED_DATA_DIR,
        "session_cookie_secure": False,
        **overrides,
    }
    return make_settings(**values)
