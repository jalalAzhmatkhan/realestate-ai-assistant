"""`alembic upgrade head` as the real, deployed schema-creation path.

Schema creation left app/main.py's lifespan and became an explicit `alembic
upgrade head` step (infra/backend/Dockerfile's ENTRYPOINT, README "Setup").
Every other test file mimics that step with `create_tables()` for speed, so
this file is the only place the actual migration runs — its job is to prove
those two paths cannot silently diverge, and that the migration itself is
reversible and re-runnable.

Alembic is driven as a subprocess rather than through `alembic.config.main`
in-process because `alembic/env.py` reads `get_settings()`, which is
`lru_cache`d and reads `backend/.env`: a subprocess with an explicit
environment is the only way to keep this hermetic against whatever the
developer's local `.env` currently says.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import build_engine, create_tables
# Imported at module scope, not inside a test: app/main.py builds `app =
# create_app()` at import time, so a late import would resolve Settings from
# backend/.env instead of the test's explicit values.
from app.main import create_app

from .conftest import SEED_DATA_DIR, SEED_PASSWORD

BACKEND_ROOT = Path(__file__).resolve().parents[1]
INITIAL_REVISION = "d0911613091d"
EXPECTED_TABLES = {
    "users",
    "properties",
    "availability_slots",
    "bookings",
    "conversations",
    "messages",
    "escalations",
}


def run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Never inherits DATABASE_URL/JWT_SECRET_KEY/LLM_PROVIDER from the developer's
    shell or backend/.env — env.py's `get_settings()` would otherwise pick them up."""
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "JWT_SECRET_KEY": "migration-test-secret-at-least-32-chars",
        "LLM_PROVIDER": "openai",
        "CORS_ALLOWED_ORIGINS": "http://localhost:5173",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def upgrade_head(db_path: Path) -> subprocess.CompletedProcess:
    result = run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    return result


def reflect(db_path: Path) -> dict:
    """Full SQLite schema: columns (name/type/nullability/default), foreign keys, and
    indexes with their uniqueness — anything coarser would let a type or a unique
    constraint drift between the migration and the models unnoticed."""
    connection = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        schema = {}
        for table in tables:
            indexes = {}
            for index in connection.execute(f"PRAGMA index_list('{table}')"):
                name, unique = index[1], bool(index[2])
                columns = [c[2] for c in connection.execute(f"PRAGMA index_info('{name}')")]
                indexes[name] = {"unique": unique, "columns": columns}
            schema[table] = {
                "columns": sorted(
                    (c[1], c[2], bool(c[3]), c[4])
                    for c in connection.execute(f"PRAGMA table_info('{table}')")
                ),
                "foreign_keys": sorted(
                    (f[2], f[3], f[4]) for f in connection.execute(f"PRAGMA foreign_key_list('{table}')")
                ),
                "indexes": indexes,
            }
        return schema
    finally:
        connection.close()


def make_migrated_db(tmp_path: Path, name: str = "migrated.db") -> Path:
    db_path = tmp_path / name
    upgrade_head(db_path)
    return db_path


def make_create_all_db(tmp_path: Path, name: str = "create_all.db") -> Path:
    db_path = tmp_path / name
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_path.as_posix()}",
        jwt_secret_key="migration-test-secret-at-least-32-chars",
        llm_provider="openai",
    )
    create_tables(build_engine(settings))
    return db_path


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> Path:
    """Module-scoped: each `alembic upgrade head` is a fresh interpreter, so the
    read-only assertions below share one rather than paying for it each time."""
    return make_migrated_db(tmp_path_factory.mktemp("migrated"))


@pytest.fixture(scope="module")
def create_all_schema(tmp_path_factory) -> dict:
    return reflect(make_create_all_db(tmp_path_factory.mktemp("create_all")))


# --- upgrade head against a fresh database ------------------------------------


def test_upgrade_head_creates_every_model_table(migrated_db):
    assert set(reflect(migrated_db)) == EXPECTED_TABLES | {"alembic_version"}


def test_upgrade_head_stamps_the_initial_revision(migrated_db):
    connection = sqlite3.connect(migrated_db)
    try:
        versions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")]
    finally:
        connection.close()

    assert versions == [INITIAL_REVISION]


# --- parity with create_tables() ----------------------------------------------
#
# The load-bearing property of this file: test fixtures build their schema with
# create_all() while every real deployment builds it with Alembic. If those two
# ever disagree, the entire suite would be testing a schema that does not exist
# in production.


def test_migrated_schema_has_exactly_the_create_all_tables(migrated_db, create_all_schema):
    migrated = reflect(migrated_db)
    del migrated["alembic_version"]  # Alembic's own bookkeeping, not a model table

    assert set(migrated) == set(create_all_schema)


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
@pytest.mark.parametrize("aspect", ["columns", "foreign_keys", "indexes"])
def test_migrated_table_matches_create_all(migrated_db, create_all_schema, table, aspect):
    assert reflect(migrated_db)[table][aspect] == create_all_schema[table][aspect]


def test_alembic_check_reports_no_drift_from_the_models(migrated_db):
    """The forward-looking guard: `alembic check` autogenerates against the live
    SQLModel metadata and fails if a model changed without a new migration. This
    catches the divergence *before* it reaches the parity assertions above."""
    result = run_alembic(migrated_db, "check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected" in result.stdout + result.stderr


# --- reversibility and re-runnability -----------------------------------------


def test_downgrade_base_drops_every_model_table(tmp_path):
    db_path = make_migrated_db(tmp_path)

    result = run_alembic(db_path, "downgrade", "base")

    assert result.returncode == 0, result.stderr
    # alembic_version survives by design (now empty) — it is Alembic's own table,
    # not something the migration created.
    assert set(reflect(db_path)) == {"alembic_version"}


def test_downgrade_base_clears_the_revision_stamp(tmp_path):
    db_path = make_migrated_db(tmp_path)
    run_alembic(db_path, "downgrade", "base")

    connection = sqlite3.connect(db_path)
    try:
        versions = list(connection.execute("SELECT version_num FROM alembic_version"))
    finally:
        connection.close()

    assert versions == []


def test_upgrade_head_is_re_runnable_after_a_full_downgrade(tmp_path):
    db_path = make_migrated_db(tmp_path)
    before = reflect(db_path)
    run_alembic(db_path, "downgrade", "base")

    upgrade_head(db_path)

    assert reflect(db_path) == before


def test_upgrade_head_on_an_already_migrated_database_is_a_no_op(tmp_path):
    """Matters because the Dockerfile entrypoint runs `alembic upgrade head` on
    *every* container start, not only the first."""
    db_path = make_migrated_db(tmp_path)
    before = reflect(db_path)

    upgrade_head(db_path)

    assert reflect(db_path) == before


# --- the app against a genuinely migrated database ----------------------------


def test_the_app_serves_requests_against_an_alembic_migrated_database(tmp_path):
    """End-to-end proof that the migration produces a schema the application can
    actually use — schema parity is necessary but not sufficient on its own."""
    db_path = make_migrated_db(tmp_path)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_path.as_posix()}",
        jwt_secret_key="migration-test-secret-at-least-32-chars",
        llm_provider="openai",
        seed_data_dir=SEED_DATA_DIR,
        session_cookie_secure=False,
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "rina.admin@evdekimi.test", "password": SEED_PASSWORD},
        )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == "admin"


def test_the_app_fails_loudly_against_an_unmigrated_database(tmp_path):
    """The other half of removing create_tables() from the lifespan: an unmigrated
    database must surface as an error, never as tables silently conjured from live
    SQLModel metadata with no migration history behind them."""
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'empty.db').as_posix()}",
        jwt_secret_key="migration-test-secret-at-least-32-chars",
        llm_provider="openai",
        seed_data_dir=SEED_DATA_DIR,
    )

    with pytest.raises(Exception):  # noqa: B017 - the DB-API error class is driver-specific
        with TestClient(create_app(settings)):
            pass


# --- env.py's connection-string wiring ----------------------------------------


def test_alembic_targets_database_url_not_the_ini_placeholder(tmp_path):
    """alembic.ini ships `driver://user:pass@localhost/dbname` as a deliberate
    placeholder; env.py must override it from Settings, or `alembic upgrade head`
    would target a database that has nothing to do with the app's."""
    db_path = tmp_path / "explicit.db"

    upgrade_head(db_path)

    assert db_path.exists()
    assert EXPECTED_TABLES <= set(reflect(db_path))
