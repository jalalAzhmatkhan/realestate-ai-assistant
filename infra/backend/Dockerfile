# Build context is ./backend (see docker-compose.yml).
#
# Two stages so the final image carries only the built virtualenv and the app
# source — not uv itself, not its download/package cache, and not any build
# toolchain a dependency's wheel might need. `UV_LINK_MODE=copy` (kept from the
# single-stage version, not the default hardlink) is what makes the venv below
# copyable into a different stage at all — a hardlinked venv would reference
# files that don't exist once `builder` isn't part of the final image.

# ---- builder: resolve and install dependencies only --------------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Only the manifest and lockfile: this stage builds the venv and nothing else,
# so it's never invalidated by an application code change, and it never needs
# the app source at all (there's no [build-system] in pyproject.toml — this
# project is not installed as a package, only its dependencies are).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---- runtime: the built venv + app source, no uv/build toolchain -------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# `alembic upgrade head` runs as an explicit step before the app starts, not from
# app/main.py's lifespan — a real deployment applies migration history rather than
# silently creating/altering tables from live SQLModel.metadata state. alembic/env.py
# reads the same DATABASE_URL (via app.core.config.Settings) the app itself connects
# with, so this targets whatever database docker-compose wires up (Postgres, which
# already has its own named volume in docker-compose.yml). Kept as a shell one-liner
# rather than a separate entrypoint.sh so the migration step and the exec'd server
# process are both visible directly in this file.
ENTRYPOINT ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
