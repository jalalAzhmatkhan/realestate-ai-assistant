"""Shared "is a real Postgres reachable" gate for the pgvector-only tests (B32/B33).

``faq_embeddings`` needs pgvector's ``vector`` column and ``CREATE EXTENSION vector``,
neither of which SQLite has — so every test that exercises the real, Postgres-backed
``FaqIndex``/migration (as opposed to the in-memory ``FaqRetriever`` double in
``tests/rag_doubles.py``) needs an actual Postgres instance. ``docker compose --env-file
backend/.env up -d postgres`` starts one on ``localhost:5433`` with the credentials this
module defaults to.

Every one of these tests is *skipped*, never silently passed, when Postgres is
unreachable — with a message loud and specific enough to say exactly what to run. See
``Documentation/audits/2026-08-06-pgvector-migration-contract.md`` decision 1(f).
"""

import os

import pytest

DEFAULT_POSTGRES_TEST_URL = (
    "postgresql+psycopg://realestate:realestate-dev-only@localhost:5433/realestate"
)
# Override to point at a different instance (e.g. CI's own Postgres service) without
# touching test code.
POSTGRES_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL_POSTGRES", DEFAULT_POSTGRES_TEST_URL
)


def _connectable(url: str) -> tuple[bool, str]:
    import psycopg

    conninfo = url.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(conninfo, connect_timeout=2):
            return True, ""
    except Exception as exc:  # pragma: no cover - exercised only when Postgres is down
        return False, str(exc)


def require_postgres(url: str = POSTGRES_TEST_DATABASE_URL) -> None:
    """Skip the calling test loudly, naming the exact remediation, if ``url`` is
    unreachable. Never lets a Postgres-only test silently pass as if it ran."""
    reachable, reason = _connectable(url)
    if not reachable:
        pytest.skip(
            f"Postgres unreachable at {url!r} ({reason}). Start it with "
            "`docker compose --env-file backend/.env up -d postgres` (or set "
            "TEST_DATABASE_URL_POSTGRES to point at one) to run this pgvector-backed "
            "check — see Documentation/audits/2026-08-06-pgvector-migration-contract.md "
            "decision 1(f). This is a loud skip, not a silent pass."
        )
