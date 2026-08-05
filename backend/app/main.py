from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.seed import seed_if_empty
from app.db.session import build_engine

API_V1_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Schema creation is NOT app startup work: it is a separate, explicit
        # `alembic upgrade head` step (see infra/backend/Dockerfile's entrypoint
        # and the README's "Setup" section for local dev) so a real deployment
        # never silently creates/alters tables from live SQLModel.metadata state
        # with no migration history or rollback path. Seeding is still startup
        # work — it only inserts rows into a schema that must already exist.
        seed_if_empty(app.state.engine, app.state.settings)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="Real Estate Agentic AI Assistant",
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # On app.state rather than module globals so a test can build an app against its
    # own settings and database; app/api/deps.py reads both from here.
    app.state.settings = settings
    app.state.engine = build_engine(settings)

    # Order matters: the catch-all registered here must end up *inside* CORSMiddleware
    # so its 500 responses still flow out through CORS header injection.
    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix=API_V1_PREFIX)

    # Operational endpoint for container/orchestrator health probes — deliberately
    # outside /api/v1 and outside the README's API surface contract.
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
