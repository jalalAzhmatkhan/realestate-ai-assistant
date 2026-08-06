import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.deps import AgentDeps
from app.api import auth, bookings, chat, properties, users
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.revocation import RedisTokenDenylist, TokenDenylist
from app.db.seed import seed_if_empty
from app.db.session import build_engine
from app.notifications.port import NotificationPort
from app.rag.embeddings import build_embedding_model
from app.rag.index import FaqRetriever
from app.rag.reindex import reindex as reindex_faq_embeddings

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

API_V1_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    token_denylist: TokenDenylist | None = None,
    agent: "Agent[AgentDeps, str] | None" = None,
    faq_index: FaqRetriever | None = None,
    notifier: NotificationPort | None = None,
    judge_model: "Model | None" = None,
) -> FastAPI:
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

        # Idempotent FAQ reindex (B32/B33): re-embeds only what changed (usually
        # nothing), so a fresh `docker compose up` yields a working FAQ path with no
        # manual step. A test that injects its own `faq_index` double is exercising a
        # substitute retriever entirely, so this is skipped for it — running it anyway
        # would mean every such test paying to load sentence-transformers only to fail
        # against a database that (correctly) has no `faq_embeddings` table on SQLite.
        # A failure here (unreachable embedding provider, etc.) must not prevent the
        # REST/auth surface or the other four tools from serving traffic — search_faq
        # degrades to its own UpstreamToolError per-request instead.
        if app.state.settings.rag_index_on_startup and app.state.faq_index is None:
            try:
                embedding_model = app.state.embedding_model or build_embedding_model(
                    app.state.settings
                )
                await reindex_faq_embeddings(
                    app.state.settings,
                    engine=app.state.engine,
                    embedding_model=embedding_model,
                )
                app.state.embedding_model = embedding_model
            except Exception:
                logger.exception(
                    "rag_index_on_startup_failed",
                    extra={"embedding_provider": app.state.settings.embedding_provider},
                )
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
    # own settings, database, and denylist binding; app/api/deps.py reads all three
    # from here. `RedisTokenDenylist.from_url` connects lazily (see its docstring),
    # so this never blocks/fails app startup even without a reachable Redis.
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.token_denylist = token_denylist or RedisTokenDenylist.from_url(settings.redis_url)

    # None means "build on first chat request" (app/api/chat.py). Deliberately not built
    # here: constructing the model requires an LLM API key, and the whole REST/auth
    # surface must boot and serve without one. A test injects a FunctionModel-backed
    # agent (and a stub-embedding-backed FaqRetriever double) so the tool-calling loop
    # runs with no network and no database at all.
    app.state.agent = agent
    # A `FaqRetriever` double injected wholesale by a test (bypassing pgvector/Postgres
    # entirely); production leaves this None, and `get_faq_index` builds a real,
    # per-request `FaqIndex` over `app.state.embedding_model` instead.
    app.state.faq_index = faq_index
    # The genuinely expensive object (a local sentence-transformers model loads torch
    # weights once per process) — built once, lazily, and reused for every request's
    # FaqIndex. None until the lifespan's startup reindex builds it, or the first chat
    # request does (app/api/chat.py::get_faq_index).
    app.state.embedding_model = None
    app.state.notifier = notifier
    # Same lazy contract as `agent`: built on the first turn that actually needs judging.
    app.state.judge_model = judge_model

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
    app.include_router(chat.router, prefix=API_V1_PREFIX)
    app.include_router(properties.router, prefix=API_V1_PREFIX)
    app.include_router(bookings.router, prefix=API_V1_PREFIX)
    app.include_router(users.router, prefix=API_V1_PREFIX)

    # Operational endpoint for container/orchestrator health probes — deliberately
    # outside /api/v1 and outside the README's API surface contract.
    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
