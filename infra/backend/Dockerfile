# Build context is ./backend (see docker-compose.yml).
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer: only re-resolves when the manifest or lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# NOTE: once B4 lands (app/db/session.py + SQLite at DATABASE_URL), the SQLite file
# needs a named volume mounted here so it survives container replacement. There is
# no DB layer yet, so no volume is declared.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
