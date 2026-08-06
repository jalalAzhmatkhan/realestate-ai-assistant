# Real Estate Agentic AI Assistant — MVP

A FastAPI backend that lets clients chat with an LLM-driven agent to get real-estate FAQ answers,
search property listings, and book or reschedule property viewings, while agents/admins get plain
RBAC-protected CRUD APIs consumed by a separate admin dashboard (see "Frontend" below). The agent's
tool selection is entirely LLM-driven via native function-calling (Pydantic AI) — there is no keyword or
if/else routing anywhere in the request path. Built as a single-service **modular monolith** whose
internal module boundaries are designed to map 1:1 onto the distributed target architecture in
[`../core components.md`](../core%20components.md) (see "Scaling to 100x" below).

> **Frontend note:** the admin dashboard is a **separate React SPA** under `frontend/admin/`, not a
> Jinja2 page inside this backend — see
> [`Documentation/system-design/frontend-admin-dashboard.md`](../Documentation/system-design/frontend-admin-dashboard.md),
> [`Documentation/ui-ux-design/admin-dashboard-ui-ux.md`](../Documentation/ui-ux-design/admin-dashboard-ui-ux.md),
> and the checkpoint that resolved this
> ([`Documentation/audits/2026-08-05-frontend-admin-spa-architecture.md`](../Documentation/audits/2026-08-05-frontend-admin-spa-architecture.md)).
> This supersedes the `app/admin/*` Jinja2 module described in earlier drafts of this doc.

> **Tool-set note:** the MVP agent exposes **five** tools. The originally-frozen four
> (`search_faq`, `SearchProperty`, `BookViewing`, `EscalateToHuman` —
> [`2026-08-05-mvp-scoping-and-tool-contracts.md`](../Documentation/audits/2026-08-05-mvp-scoping-and-tool-contracts.md))
> were extended with `RescheduleViewing` by Project Manager scope decision
> ([`2026-08-05-mvp-scope-decisions-prompt-editor-reschedule-tool.md`](../Documentation/audits/2026-08-05-mvp-scope-decisions-prompt-editor-reschedule-tool.md)),
> contract in
> [`2026-08-05-reschedule-viewing-tool-contract.md`](../Documentation/audits/2026-08-05-reschedule-viewing-tool-contract.md).
> The set is closed at five.

## Setup

Requirements: Python >= 3.11, [`uv`](https://docs.astral.sh/uv/).

```bash
cd realestate-ai-assistant/backend
uv sync                      # installs dependencies from pyproject.toml/uv.lock
cp .env.example .env         # then fill in the values described below
uv run alembic upgrade head  # applies schema migrations — required before first boot, see below
uv run uvicorn app.main:app --reload   # once app/main.py exists; current main.py is a placeholder
```

The Python project root is `backend/` — see the Module Layout note below for the state of that move.

### Database migrations (Alembic)

Schema changes are managed by [Alembic](https://alembic.sqlalchemy.org/) (`backend/alembic/`,
`backend/alembic.ini`), not by the application at startup. `app/main.py`'s lifespan no longer calls
`SQLModel.metadata.create_all()` — a real deployment applies migration history explicitly, with a
rollback path, rather than having the app silently create or (more importantly) *fail to alter* tables
from whatever the current model code happens to declare.

```bash
uv run alembic upgrade head      # apply all pending migrations (run this before first boot, and again
                                  # after pulling any change that adds a migration)
uv run alembic downgrade -1      # roll back one migration
uv run alembic revision --autogenerate -m "describe the change"   # generate a new migration after
                                                                    # editing app/models/*
```

`alembic/env.py` reads the connection string from `app.core.config.get_settings().database_url` (i.e.
`DATABASE_URL`/`backend/.env`), the same place `app/db/session.py` reads it from — never a hardcoded
value or `alembic.ini`'s static `sqlalchemy.url` — so `alembic upgrade head` targets the same database
the app itself would connect to, whether that's the SQLite dev default or the Postgres DSN
docker-compose injects. Running the app in Docker applies migrations automatically as part of container
startup (see `infra/backend/Dockerfile`'s entrypoint); non-Docker local dev (`uv run uvicorn`) requires
running `alembic upgrade head` yourself first, as shown above.

On first startup, `app/db/seed.py` checks whether the configured database is empty and, if so, loads
`backend/seed_data/*.json` (users, properties, FAQ, availability, viewings) so the app is immediately
usable without a manual seeding step. Set `SEED_ON_STARTUP=false` to disable this (e.g. once you have
real data and don't want seed data re-applied against a non-empty DB — the loader still no-ops on a
non-empty DB by default, but the flag gives an explicit kill switch). Seeding still runs from
`app/main.py`'s lifespan — only schema creation moved to Alembic — but it now requires the schema to
already exist (i.e. `alembic upgrade head` to have run); against an unmigrated database it fails loudly
at startup instead of inserting into tables that were never created.

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_ENV` | no | `dev` | `dev`/`prod`, controls log format and docs exposure |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `DATABASE_URL` | no [^db-url-transitional] | `postgresql+psycopg://realestate:realestate-dev-only@localhost:5432/realestate` | **Postgres required** — pgvector backs the FAQ index (Design Decisions §6). SQLite is not a supported runtime |
| `SEED_DATA_DIR` | no | `./seed_data` | Where the seed loader reads JSON from, resolved relative to the process working directory (`backend/`) — i.e. `backend/seed_data/` from the repo root |
| `SEED_ON_STARTUP` | no | `true` | Load seed data into an empty DB on boot |
| `JWT_SECRET_KEY` | **yes** | — | Signing secret for locally-issued JWTs |
| `JWT_ALGORITHM` | no | `HS256` | JWT signing algorithm |
| `JWT_EXPIRE_MINUTES` | no | `60` | Access token TTL |
| `SESSION_COOKIE_NAME` | no | `session` | Name of the httpOnly session cookie set for `client_type=browser` logins |
| `SESSION_COOKIE_SECURE` | no | `true` | Sets the `Secure` cookie flag; may be `false` only for local `http://localhost` dev |
| `LLM_PROVIDER` | **yes** | — | `openai` \| `anthropic` \| `gemini` — selects which Pydantic AI `Model` the provider factory builds for chat/tool-calling |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | if `LLM_PROVIDER=openai` | model default `gpt-4o-mini` | OpenAI credentials/model |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | if `LLM_PROVIDER=anthropic` | model default `claude-sonnet-4-5` | Anthropic credentials/model |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | if `LLM_PROVIDER=gemini` | model default `gemini-2.5-flash` | Gemini credentials/model |
| `EMBEDDING_PROVIDER` | no | `local` | `openai` \| `gemini` \| `local` — **independent** of `LLM_PROVIDER`, see Design Decisions |
| `OPENAI_EMBEDDING_MODEL` | no | `text-embedding-3-small` | Used only when `EMBEDDING_PROVIDER=openai` |
| `GEMINI_EMBEDDING_MODEL` | no | `text-embedding-004` | Used only when `EMBEDDING_PROVIDER=gemini` |
| `LOCAL_EMBEDDING_MODEL` | no | `sentence-transformers/all-MiniLM-L6-v2` | Used only when `EMBEDDING_PROVIDER=local` |
| `RAG_TOP_K` | no | `3` | Default number of chunks `search_faq` retrieves |
| `RAG_MIN_SCORE` | no | `0.55` | Cosine-similarity floor below which `search_faq` reports "no relevant answer" instead of forcing a low-confidence answer |
| `RAG_INDEX_ON_STARTUP` | no | `true` | Run the idempotent FAQ reindex during app startup. No-op and no embedding calls when the index is current |
| `DEFAULT_PAGE_SIZE` | no | `20` | Default `page_size` for list endpoints |
| `MAX_PAGE_SIZE` | no | `100` | Hard ceiling for `page_size`; larger values are rejected (422), not silently clamped |
| `CORS_ALLOWED_ORIGINS` | no | `*` (dev only) | Comma-separated origins; must be an explicit origin list (not `*`) for the cookie-authenticated admin SPA |

[^db-url-transitional]: As of B31 (pgvector infrastructure — this task), `Settings.database_url`'s
  code default has **not** been flipped yet and is still `sqlite:///./app.db`; only `docker-compose.yml`'s
  `postgres` service image, the `pgvector` dependency, and `RAG_INDEX_ON_STARTUP` land in this task. The
  row above documents the target state once B32 (`faq_embeddings` schema + Alembic migration, which is
  what actually makes `CREATE EXTENSION vector` run and makes SQLite fail `alembic upgrade head`) lands —
  see `Documentation/audits/2026-08-06-pgvector-migration-contract.md`. B31–B33 are a hard gate sequenced
  together, so this is transitional only for the span of that sequence.

`.env.example` (to be created by the Backend Engineer alongside implementation, at `backend/.env.example`)
should enumerate all of the above with safe dev defaults and empty API key placeholders.

## Module Layout

```
realestate-ai-assistant/
  backend/                   # Python service root — the uv project lives here
    main.py                  # existing placeholder entrypoint (superseded by app/main.py)
    pyproject.toml
    .python-version
    .env.example
    seed_data/               # see "Seed Data" below
    app/
      api/                   # HTTP layer: FastAPI routers, request/response wiring only
        deps.py              #   get_current_user / require_role() dependencies
        auth.py              #   POST /auth/login (+ Set-Cookie/CSRF for the SPA), POST /auth/logout, GET /auth/me
        chat.py              #   POST /chat/messages — entrypoint into app/agent
        properties.py        #   property CRUD + read endpoints (RBAC-scoped)
        bookings.py          #   booking CRUD/list endpoints (RBAC-scoped)
        users.py             #   admin-only user management endpoints
        pagination.py        #   shared PageParams dependency + Page[T] response envelope
      agent/                 # the agentic core
        providers.py         #   LLM_PROVIDER -> Pydantic AI Model factory
        prompt.py            #   SYSTEM_PROMPT — the cross-tool reasoning layer (load-bearing, not docs)
        orchestrator.py      #   Agent construction (all 5 tools registered up front), tool-call extraction
        deps.py              #   AgentDeps: authenticated user + db session + rag index + notification port
        tools/
          errors.py          #   ToolError (ToolFailed, never ModelRetry) — see Design Decisions
          booking_common.py  #   maps app/booking/slots.py's exceptions -> ToolError + model-facing guidance
          search_faq.py
          search_property.py
          schedule_viewing.py
          reschedule_viewing.py
          escalate_to_human.py
      booking/               # booking domain logic shared by the booking tools and the REST endpoints
        slots.py             #   availability resolution + conflict detection (single source of truth)
        queries.py           #   scoped_booking_query(user) — shared RBAC scoping for RescheduleViewing,
                              #   GET /bookings, cancel, and the REST reschedule endpoint (Phase 4)
      property/
        queries.py           #   scoped_property_query(user) — shared RBAC scoping for SearchProperty,
                              #   GET /api/v1/properties, and escalate_to_human's assignment lookup
      rag/                   # retrieval for search_faq
        embeddings.py        #   EMBEDDING_PROVIDER -> Pydantic AI EmbeddingModel factory (independent of LLM_PROVIDER)
        index.py             #   in-process flat vector index (numpy cosine similarity)
      notifications/         # async side-effect abstraction
        port.py              #   NotificationPort protocol (publish booking/escalation events)
        log_notifier.py      #   MVP implementation: structured log line
      core/                  # cross-cutting concerns
        config.py            #   pydantic-settings Settings, reads .env
        security.py          #   password hashing, JWT encode/decode, CSRF token issue/verify, role checks
        logging.py           #   structured logging setup
        exceptions.py        #   domain exceptions -> HTTP status mapping
      models/                # SQLModel ORM entities
        user.py / property.py / booking.py / availability_slot.py / escalation.py / conversation.py
      schemas/               # Pydantic request/response DTOs (kept separate from ORM models)
        auth.py / chat.py / property.py / booking.py / user.py
      db/
        session.py           # engine/session factory (DATABASE_URL-driven)
        seed.py              # loads backend/seed_data/*.json into an empty DB on startup
    alembic/                 # migration environment (env.py reads DATABASE_URL via Settings)
      versions/              # one file per migration, e.g. initial schema for all 6 SQLModel entities
    alembic.ini
  frontend/
    admin/                   # separate React (Vite + TS) SPA — own package.json, see
                             #   Documentation/system-design/frontend-admin-dashboard.md
```

> **This is the target layout, partially realized.** `backend/seed_data/` already exists; moving
> `main.py`, `pyproject.toml`, and `.python-version` under `backend/` is task **R1**, the first task in
> `Documentation/audits/2026-08-05-mvp-implementation-sequencing.md` (ruling A1), and has not been
> executed yet. Until it is, those three files are still at the repo root. `app/` does not exist yet and
> is created directly at `backend/app/`.

> **No `app/models/system_prompt.py`.** The system-prompt editor screen was deferred out of MVP scope
> (`Documentation/audits/2026-08-05-mvp-scope-decisions-prompt-editor-reschedule-tool.md` decision #1),
> so the model backing it is deliberately absent rather than scaffolded. The system prompt is authored
> in code and changed by a deploy.

`seed_data/` sits alongside `app/` inside `backend/` rather than nested under `app/` so it can be
inspected, edited, and diffed without importing the `app` package, and so `SEED_DATA_DIR` can point at a
different location (e.g. an integration-test fixtures folder) without touching code.

## Design Decisions

### 1. Modular monolith now, microservices later

`core components.md` defines the target architecture at 100x scale: 8 independently-deployed
services plus Redis/Kafka/Elasticsearch/PostGIS/a vector DB. Building that for a take-home is the
wrong trade-off — it would spend the assessment's time budget on infrastructure wiring instead of on
agentic design and code quality, which is 55% of the scored weight (Architecture 30% + Agentic AI
Design 25%) with Backend Quality being a separate 20%. A solo developer standing up 8 services,
Kafka, Elasticsearch, and a vector DB for a demo is also directly at odds with "we value ... engineering
judgment more than feature completeness."

Instead, this MVP is **one FastAPI process, internally partitioned into modules with the exact same
responsibility boundaries as the target services**. Each module talks to the others through a narrow
internal interface (a Python function/class call today, an HTTP/gRPC/event call tomorrow) rather than
reaching into another module's data model directly. This is what makes the "how does this scale to
100x" story a *mechanical extraction*, not a redesign:

| MVP module | Future service (`core components.md`) | Extraction trigger / notes |
|---|---|---|
| `app/api/chat.py` | Chat API Service | First candidate to split off — thin, stateless, easy to peel away once session state moves to Redis |
| `app/agent/*` | Agent Orchestrator | Second candidate — LLM-latency-bound, benefits from independent autoscaling vs. the CRUD paths |
| `app/agent/tools/search_faq.py` + `app/rag/*` | Knowledge Base (RAG) | Extract when the FAQ corpus outgrows an in-process index (see RAG section below) |
| `app/agent/tools/search_property.py` + read paths of `app/api/properties.py` | Search Service (Elasticsearch) | Extract when read QPS or query complexity (geo/faceted) exceeds what SQL filtering handles well |
| `app/api/properties.py` (write paths) + `app/models/property.py` | Property Service (PostgreSQL + PostGIS) | Extract when listings need independent write scaling / geo indexing |
| `app/agent/tools/schedule_viewing.py` + `app/agent/tools/reschedule_viewing.py` + `app/api/bookings.py` + `app/booking/*` + `app/models/booking.py` | Booking Service | Extract when booking write volume or conflict-check latency needs isolation from chat traffic. All three booking write paths already funnel through `app/booking/slots.py`, so extraction moves one module rather than three copies of the conflict check |
| `app/agent/tools/escalate_to_human.py` + `app/models/escalation.py` | New Escalation/Support routing capability (not in original `core components.md`; see Open Questions) | Extract once real human-agent routing/CRM integration exists |
| `app/notifications/*` | Notification Service (queue consumer) | Swap `NotificationPort` implementation from `LogNotifier` to a queue publisher — callers never change |
| `app/core/security.py` + `app/api/auth.py` + `app/models/user.py` | Auth Service | Extract when multiple services need centralized token issuance/validation |
| `frontend/admin/*` | Not a backend service — a separate SPA client of the same REST API every other client uses | Deployed/scaled independently (static hosting/CDN) regardless of backend extraction; see `Documentation/system-design/frontend-admin-dashboard.md` |
| SQLite (dev) / single Postgres (prod-ish) via SQLModel | PostgreSQL + PostGIS (Property), separate Postgres per service after extraction | Swap `DATABASE_URL`; geo queries move from Python Haversine to `ST_DWithin` |
| No Redis in MVP | Redis (session/cache) | Add once session state must be shared across many stateless instances |
| `LogNotifier` (structured log line) | Kafka/RabbitMQ/SQS + Notification Service | Swap the `NotificationPort` implementation only |

Because every module already receives its dependencies (DB session, RAG index, notification port)
through constructor/parameter injection rather than global state, "extraction" in the future is:
move the module's code into a new deployable, replace its in-process call sites with a client
(HTTP/gRPC/event) that implements the same interface, and add the new service to routing config. No
internal module is designed to assume it will always be co-located with the others.

### 2. FastAPI + Pydantic AI + a modular LLM provider

`app/agent/providers.py` reads `LLM_PROVIDER` at startup and constructs the corresponding Pydantic AI
`Model` (`OpenAIModel`, `AnthropicModel`, or `GoogleModel`/Gemini equivalent), each parameterized by
its own `*_API_KEY` / `*_MODEL` env vars. `app/agent/orchestrator.py` builds a single `pydantic_ai.Agent`
against whichever `Model` the factory returns and never branches on provider elsewhere in the codebase.
Switching providers is a `.env` change, not a code change — this is the concrete implementation of
"easily extensible for future agents/services," and it also means adding a fourth provider later is a
one-function change in `providers.py`. The `Model` is built by a **factory function, not an import-time
singleton**, so a future tiered `get_model(tier)` differs by one parameter instead of a rewrite of the
orchestrator's construction path.

Model routing (cheap/fast model for FAQ-style intents vs. a stronger model for multi-step reasoning),
which `CLAUDE.md`/`core components.md` describe as a target-state cost control, is **deliberately out
of scope for the MVP** — a single configured model handles every turn. Flagged explicitly in the
checkpoint as a scope reduction, not an oversight; the reasoning, and the trigger that would justify
building it, are recorded in
`Documentation/audits/2026-08-05-pm-signoffs-model-routing-and-escalation-assignment.md` Decision 1.

### 3. Autonomous tool selection, no if/else routing

The agent is registered with all five tools up front; on each turn, the underlying LLM's native
function-calling decides whether to call a tool, which one, and with what arguments, based on the
tool's docstring/schema and the conversation so far. There is no `if "book" in message` /
intent-classifier-with-hardcoded-branches anywhere in `app/agent` or `app/api/chat.py` — the only
"routing" logic that exists is RBAC re-authorization *inside* each tool after the LLM has already
chosen to call it (see #5). This directly satisfies the assessment's explicit "NO keyword-based
if/else routing" minimum requirement.

`BookViewing` vs. `RescheduleViewing` is a live illustration of why this matters: "actually, can we
make it Monday instead?" is a reschedule, while "can I also see the other unit on Monday?" is a new
booking. The difference is contextual, not lexical — both mention a day and a viewing. Distinguishing
them is the model's job, driven by the two tools' descriptions and the conversation history; it is
exactly the case a keyword router would get wrong.

### 4. Tool-name mapping to the assessment's required names

The assessment requires tools literally named `SearchProperty`, `BookViewing`, `EscalateToHuman`.
`CLAUDE.md`'s pre-existing design used snake_case (`search_property`, `schedule_viewing`) consistent
with Python/Pydantic AI convention. Decision: **keep snake_case Python function/module names for
readability, but expose the tool to the LLM (and in the OpenAPI/tool schema) under the literal
required name** via Pydantic AI's tool `name=` parameter. This removes any ambiguity for a grader
inspecting tool schemas or traces, at zero cost to code quality.

| Assessment-required name | Exposed tool name (`name=`) | Implementing module | Notes |
|---|---|---|---|
| `SearchProperty` | `SearchProperty` | `app/agent/tools/search_property.py` (`search_property()`) | Structured + geo property search |
| `BookViewing` | `BookViewing` | `app/agent/tools/schedule_viewing.py` (`schedule_viewing()`) | Kept as `schedule_viewing` internally for continuity with `CLAUDE.md`/`core components.md`, exposed as `BookViewing` |
| `EscalateToHuman` | `EscalateToHuman` | `app/agent/tools/escalate_to_human.py` (`escalate_to_human()`) | New tool, not in the original 3-tool draft — added specifically to satisfy this requirement |
| (not literally required) | `search_faq` | `app/agent/tools/search_faq.py` (`search_faq()`) | Satisfies objective #1 (FAQ/RAG); no mandated literal name, left snake_case |
| (not literally required) | `RescheduleViewing` | `app/agent/tools/reschedule_viewing.py` (`reschedule_viewing()`) | MVP scope addition beyond the assessment minimum (PM decision). PascalCase by decision, not by requirement — see below |

**Naming decision for `RescheduleViewing`.** It is **not** one of the assessment's mandated literal
names, so either convention would satisfy the grading criteria — it could legitimately have stayed
`reschedule_viewing`, like `search_faq`. Decision: **expose it as `RescheduleViewing` (PascalCase).**
Rationale: its exposed name sits next to `BookViewing` in the same tool schema, covers the same domain,
and is frequently weighed against `BookViewing` in the same turn; `BookViewing` alongside
`reschedule_viewing` reads as an inconsistency both to a grader inspecting tool traces and to the model
choosing between them. `search_faq` stays snake_case because it has no PascalCase sibling to be
inconsistent with. Trade-off accepted: exposed names are now split by domain (booking tools PascalCase,
FAQ snake_case) rather than uniform — a cosmetic inconsistency smaller than the one it avoids. Python
function and module names remain snake_case throughout, unchanged.

### 5. RBAC re-authorization at the tool layer

`app/api/chat.py` resolves the caller's identity from the JWT (`app/core/security.py`) into an
`AuthenticatedUser(id, role, email)` **before** the agent ever runs, and passes it into the Pydantic AI
run as `deps=AgentDeps(user=..., db=..., rag=...)`. Tool functions receive `ctx: RunContext[AgentDeps]`
alongside their LLM-supplied arguments and re-check every trust-sensitive field against
`ctx.deps.user` rather than trusting what the LLM passed:

- `schedule_viewing`: if `ctx.deps.user.role == "client"`, the tool **overrides** any `client_id`
  argument the LLM supplied with `ctx.deps.user.id` — a client can never book on someone else's behalf
  no matter what the model generates. `agent`/`admin` callers may supply a `client_id` on behalf of a
  client, but the tool validates that ID exists and (for `agent`) that the target property belongs to
  that agent.
- `reschedule_viewing`: the input schema carries **no identity fields at all**. The booking to move is
  resolved server-side against the caller's RBAC-scoped booking set (`client` -> own `client_id`,
  `agent` -> own `agent_id`, `admin` -> any), so an LLM-supplied `booking_id` outside that scope
  resolves to "not found" instead of being honored. See the tool contract below.
- `search_property`: no identity argument exists in the schema at all for the default case; result
  visibility is filtered by `ctx.deps.user.role` (see RBAC table below), not by an LLM-supplied filter.
- `escalate_to_human`: always tied to `ctx.deps.user.id` / the current `conversation_id`; a user cannot
  escalate a different session. Its `property_id` lookup for listing-agent assignment reuses the same
  RBAC-scoped property query `search_property` uses, so an LLM-supplied id cannot reveal a listing the
  caller may not see.

This mirrors `core components.md` §7's "tool calls are re-authorized, not trusted" principle exactly —
collapsing the network hop into an in-process call does not relax the trust boundary.

### 6. RAG approach for `search_faq`

Chosen: **pgvector**. FAQ entries from `backend/seed_data/faq.json` are embedded once by an explicit
indexing step and stored in a `faq_embeddings` table with a `vector` column; `search_faq` retrieves via a
cosine-distance query (`<=>`) rather than an in-process similarity scan. Specified in
`Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md` decision 1, which supersedes the
in-process NumPy index this section previously described.

`EMBEDDING_PROVIDER` remains **intentionally decoupled from `LLM_PROVIDER`**: Anthropic has no embeddings
API, so with `LLM_PROVIDER=anthropic` embeddings still come from `openai`, `gemini`, or a local
`sentence-transformers` model (`EMBEDDING_PROVIDER=local`, the zero-API-key default).

**Dimensionality.** The three providers emit different widths (local MiniLM-L6-v2 → 384,
`text-embedding-004` → 768, `text-embedding-3-small` → 1536), so the column is declared `vector` with
**no dimension modifier** and every row records the width it actually got. Application code never
hardcodes a provider→dimension map — OpenAI's model accepts a `dimensions` parameter, so any such map is
wrong the first time it is used. An `embedding_model` discriminator column scopes every query, so
switching provider writes a new row set and leaves the old one intact: a provider switch is a re-run of
the reindex, not a migration.

**No ANN index (HNSW/IVFFlat), deliberately.** At this corpus size an exact scan is faster than any index
— but the decisive reason is correctness, not speed: an approximate index would make the Recall@K/MRR
evaluation measure the index's recall convolved with embedding quality, so the metric would stop
measuring the thing it reports. Read that checkpoint's decision 3(e) before adding one.

**Populating the index** is an explicit, idempotent step, not a migration and not a lazy first-request
build:

    uv run python -m app.rag.reindex          # embeds only what changed (sha256 of the embedded document)
    uv run python -m app.rag.reindex --force  # re-embeds everything for the configured model

`RAG_INDEX_ON_STARTUP` (default `true`) runs the same routine in the app lifespan, so
`docker compose up` on a fresh volume yields a working FAQ path with no extra step; when the index is
current it is a hash comparison and zero embedding calls. It is deliberately **not** part of
`alembic upgrade head`: a migration must be deterministic and runnable without credentials, and embedding
calls are neither.

`backend/seed_data/faq.json` remains the source of truth for FAQ content; `faq_embeddings` is a derived
cache that can be dropped and rebuilt at any time.

**An empty index is an error, not an empty result.** If `faq_embeddings` holds no rows for the configured
`embedding_model`, `search_faq` raises rather than returning `[]` — an empty result list is a
*successful* "nothing matched confidently", so an unpopulated index would otherwise disable FAQ answering
system-wide with nothing appearing broken. A populated index with no match above `RAG_MIN_SCORE` still
returns `[]`, unchanged.

Trade-off accepted: Postgres is now a hard runtime requirement (see §7), and there is a step between
"schema exists" and "FAQ search works". In exchange, embeddings are computed once rather than per
process, survive restarts, are inspectable with a SQL client without the app running, and the corpus is
no longer bounded by what fits in every worker's memory.

> **Implementation sequencing note (B31–B33).** This section describes the pgvector design in full, but
> the `faq_embeddings` table, the Alembic migration, and the reindex command it describes are **not yet
> implemented as of B31** (this task is infrastructure-only: the `pgvector/pgvector:pg16` image, the
> `pgvector` Python dependency, and `RAG_INDEX_ON_STARTUP`). `app/rag/index.py` still runs the in-process
> NumPy scan until B32 (schema/migration) and B33 (retrieval rewrite) land. See
> `Documentation/audits/2026-08-06-pgvector-migration-contract.md` for the task breakdown; B31 → B32 → B33
> is a hard gate sequenced together.

### 7. Persistence

PostgreSQL via SQLModel/SQLAlchemy (`DATABASE_URL=postgresql+psycopg://...`). **Postgres is required, not
optional:** the FAQ index uses the pgvector extension (§6), so `CREATE EXTENSION vector` and a `vector`
column are part of the schema and `alembic upgrade head` cannot run on SQLite. The shortest local path is
`docker compose up -d postgres` (image `pgvector/pgvector:pg16`), then `alembic upgrade head`, then
`uv run python -m app.rag.reindex`.

SQLite is still used by the test suite for every table except `faq_embeddings`; retrieval tests and the
migration-parity test run against real Postgres and are skipped with an explicit marker when it is
unavailable, never silently passed. Agent and chat tests inject a stub retriever via
`create_app(faq_index=...)` and need no vectors at all.

Geo "near me" search in `search_property` still uses a Haversine distance computed in Python over the
(small) seeded dataset — explicitly **not** how this should work at scale; the documented production path
is PostGIS `ST_DWithin`/`ST_Distance`, which is now a smaller step than it was, since Postgres is already
a hard dependency.

> **Implementation sequencing note (B31–B33), same caveat as §6.** As of B31 (this task), Postgres is not
> yet a hard runtime requirement in code — `Settings.database_url` still defaults to SQLite, and the
> three-tier test strategy described above (SQLite for most tests, a stub retriever for agent/chat tests,
> real Postgres for retrieval and migration-parity tests) applies once B32/B33 land the
> `faq_embeddings` table and the pgvector-backed retrieval path. This section documents the target state
> of the full B31–B33 sequence, applied here per the README patch mapping in
> `Documentation/audits/2026-08-06-pgvector-migration-contract.md`.

Schema is versioned with Alembic (`backend/alembic/`), not `SQLModel.metadata.create_all()` at app
startup — `alembic upgrade head` is a separate, explicit step (local dev: run it yourself before
`uv run uvicorn`; Docker: run automatically by `infra/backend/Dockerfile`'s entrypoint before
`uvicorn` starts). `create_all()` can add a table that's missing from a live database but can never
alter one that already exists and leaves no migration history or rollback path — fine for a
never-deployed prototype, not for a service anyone runs twice. Test fixtures are the one place that
still call `create_all()` directly, against a throwaway per-test SQLite file — running real Alembic
migrations for every test would be slow and would be testing Alembic, not application code; the
schema-shape produced by the initial migration is asserted to match `create_all()`'s output exactly, so
the two are not permitted to silently diverge.

### 8. Async side-effects without a message queue

`app/notifications/port.py` defines a `NotificationPort` protocol (`publish_booking_created`,
`publish_booking_rescheduled`, `publish_booking_cancelled`, `publish_escalation_created`); the MVP
binds it to `LogNotifier`, which writes a structured log line instead of sending real email/SMS. This
preserves the target architecture's principle that booking/escalation side-effects are decoupled from
the request path (callers never block on notification delivery) while avoiding a Kafka/RabbitMQ/SQS
dependency for a demo. Swapping to a real queue publisher later touches only `app/notifications/`,
nothing upstream.

`publish_booking_rescheduled` is emitted by **both** reschedule call sites (the `RescheduleViewing`
tool and the REST endpoint) with an identical payload, so a downstream consumer cannot tell — and does
not need to care — whether a reschedule originated in chat or in the dashboard.

`publish_escalation_created` has a single call site and carries the escalation's `assigned_agent_id`
and `status`, so a future queue consumer knows who an escalation is for without re-querying the DB. It
deliberately omits `reason`/`conversation_summary`: those are free text that may contain personal
detail the user typed, and a fan-out event is the wrong place for it — a consumer that needs them
fetches by `escalation_id`.

### 9. One booking-conflict implementation, three call sites

Viewing slots are resolved and conflict-checked in exactly one place — `app/booking/slots.py` — which
is called by all three booking write paths:

| Call site | Surface | Operation |
|---|---|---|
| `schedule_viewing` (`BookViewing`) tool | Chat / LLM | Claim a slot for a new booking |
| `reschedule_viewing` (`RescheduleViewing`) tool | Chat / LLM | Release the old slot, claim a new one |
| `POST /api/v1/bookings/{id}/reschedule` | Admin SPA / REST | Release the old slot, claim a new one |

Three copies of "is this slot free" would inevitably diverge, and the chat surface and the dashboard
disagreeing about availability is a data-integrity bug, not a cosmetic one. **Sequencing constraint:**
`app/booking/slots.py` must be extracted *before* either booking tool or the REST endpoint is
implemented, so none of them ships with a private copy.

The same pattern applies once more, in miniature: the RBAC-scoped property query is shared by
`search_property`, `GET /api/v1/properties`, and `escalate_to_human`'s assignment lookup, extracted as
one helper for the same reason — three surfaces disagreeing about what a user may see is an
authorization bug, not a style problem.

Reschedule is a **distinct operation, not cancel-then-rebook**: it keeps the same `booking_id` (so the
client's reference and any existing notification thread stay valid), preserves booking history, and
emits a single `publish_booking_rescheduled` event instead of a cancel+create pair that downstream
consumers would have to correlate. The slot swap (release old availability slot, claim new one, update
the booking row) happens in one DB transaction so a failure mid-way cannot leave a booking pointing at
a released slot. The two reschedule call sites differ only in how the booking is *identified* (path
parameter vs. server-side resolution from conversational hints) and in how errors are *rendered* (HTTP
status vs. `ToolError`); the state transition itself is one code path. See both reschedule contracts
below.

## API Surface (summary)

| Endpoint | Method | Roles | Purpose |
|---|---|---|---|
| `/api/v1/auth/login` | POST | any | Exchange email/password for a session. `client_type=api` (default) returns a Bearer JWT in the body; `client_type=browser` sets an httpOnly session cookie and returns a CSRF token instead |
| `/api/v1/auth/logout` | POST | admin, agent, client | Revokes the presented token immediately via a Redis-backed denylist and clears the session cookie (browser sessions only) |
| `/api/v1/auth/me` | GET | admin, agent, client | Returns the current user's id/name/email/role (+ a fresh CSRF token for cookie sessions) so the SPA can render role-aware UI without decoding the httpOnly cookie |
| `/api/v1/chat/messages` | POST | admin, agent, client | Send a message to the agent; entrypoint to the tool-calling loop |
| `/api/v1/properties` | GET | admin, agent, client | List/search properties (paginated + filtered, RBAC-scoped, see below) |
| `/api/v1/properties/{id}` | GET | admin, agent, client | One listing in full (`PropertyDetail`), scoped to what the caller may *see*, not what they may edit |
| `/api/v1/properties` | POST | admin, agent (own) | Create a listing |
| `/api/v1/properties/{id}` | PUT/DELETE | admin, agent (own) | Update / deactivate a listing |
| `/api/v1/bookings` | GET | admin, agent (own), client (own) | List bookings (paginated + filtered, RBAC-scoped) |
| `/api/v1/bookings/{id}` | GET | admin, agent (own), client (own) | One booking |
| `/api/v1/bookings/{id}/cancel` | POST | admin, agent (own), client (own) | Cancel a booking |
| `/api/v1/bookings/{id}/reschedule` | POST | admin, agent (own), client (own) | Move a booking to a different slot, keeping the same `booking_id` |
| `/api/v1/users` | GET/POST | admin | List users (paginated + filtered) / create a user |
| `/api/v1/users/{id}` | GET/PATCH | admin | Read / update a user |

The admin dashboard (`frontend/admin/`) is a client of this same API — it has no dedicated backend
routes of its own. See `Documentation/system-design/frontend-admin-dashboard.md` §7 for the full
screen-to-endpoint mapping.

### Error response shape

Validation errors (`422`) use FastAPI/Pydantic's native error list unchanged, so the SPA can map them
to individual form fields (see the frontend design doc §8). All other domain errors return a
structured `detail` object with a stable machine-readable `code`:

```
Response 4xx/5xx:
{
  "detail": {
    "code": "booking_slot_conflict",     // stable, safe to branch on in clients
    "message": "That slot is already booked.",
    "suggested_alternatives": [...]      // optional, error-specific extra fields
  }
}
```

Clients branch on `code`, never on `message` (which is human-facing and may be reworded or localized).

### Chat endpoint contract

```
POST /api/v1/chat/messages
Authorization: Bearer <jwt>

Request:
{
  "conversation_id": "conv-123" | null,   // null starts a new conversation
  "message": "string"
}

Response 200:
{
  "conversation_id": "conv-123",
  "reply": "string",
  "tool_calls": [
    { "tool": "SearchProperty", "arguments": {...}, "result_summary": "3 matches found" }
  ],
  "created_at": "2026-08-05T10:00:00+07:00"
}
```

### Auth contracts (login, logout, me)

Two authentication modes exist against the same user store. Which one a login produces is chosen
**explicitly by the caller** via the `client_type` request field, not inferred from `Origin`/
`User-Agent`/CORS config — an implicit rule would be both spoofable and invisible in the OpenAPI
schema, and would make the two modes untestable in isolation.

| Mode | `client_type` | Credential returned | Sent on subsequent requests as | CSRF needed |
|---|---|---|---|---|
| Programmatic (default) | `api` | `access_token` in JSON body | `Authorization: Bearer <jwt>` | No — no ambient credential |
| Browser SPA | `browser` | httpOnly session cookie + `csrf_token` in JSON body | Cookie (automatic) + `X-CSRF-Token` header | Yes, on state-changing methods |

#### `POST /api/v1/auth/login`

```
POST /api/v1/auth/login
Content-Type: application/json

Request:
{
  "email": "rina.admin@evdekimi.test",
  "password": "string",
  "client_type": "api" | "browser"        // optional, default "api"
}

Response 200 (client_type = "api"):
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 3600,                     // seconds, mirrors JWT_EXPIRE_MINUTES
  "csrf_token": null,
  "user": { "id": "u-admin-1", "name": "Rina Marpaung",
            "email": "rina.admin@evdekimi.test", "role": "admin" }
}

Response 200 (client_type = "browser"):
Set-Cookie: session=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=3600
{
  "access_token": null,                   // deliberately withheld — see below
  "token_type": null,
  "expires_in": 3600,
  "csrf_token": "9f2c...43-char-base64url",
  "user": { "id": "u-admin-1", "name": "Rina Marpaung",
            "email": "rina.admin@evdekimi.test", "role": "admin" }
}
```

- **Roles:** unauthenticated endpoint; any valid credential of any role succeeds. The login endpoint
  does **not** reject `client` role for `client_type=browser` — role-gating the admin dashboard is the
  SPA's concern (it shows "this dashboard is for staff"), and the same browser session type will be
  needed if a browser-based client chat surface is ever built. Backend RBAC on each admin endpoint is
  the real boundary, per the frontend design doc §2.
- **`access_token` is `null` in browser mode, by design.** Returning both the cookie and the raw token
  would put a Bearer credential into JavaScript memory, which is exactly what httpOnly is meant to
  prevent — an XSS payload could exfiltrate it and replay it outside the browser, bypassing
  `SameSite=Strict` and CSRF entirely. The field is kept in the schema (as `null`) rather than removed
  so both modes share one response model.
- **Failure modes:** unknown email or wrong password -> `401 {"code": "invalid_credentials"}` with an
  identical message for both cases (no user-enumeration oracle); disabled user (`status="disabled"`,
  see the user model note under list endpoints) -> `403 {"code": "account_disabled"}`; malformed body
  -> `422`.
- **Login rate limiting is target-state, not implemented — `429 too_many_attempts` is never returned
  by this MVP.** In the target architecture, repeated failed logins from the same IP/email return
  `429 {"code": "too_many_attempts"}`, enforced at the API Gateway. It is deliberately absent here for
  the same class of reason model routing is (Design Decisions §2): correct rate limiting is per-IP/
  per-email *shared counter state*, so an in-process counter would be wrong the moment there is more
  than one instance and would contradict `CLAUDE.md`'s stateless-services principle — an architectural
  concession bought for a control that a demo does not exercise. Deferred by ruling **A2** in
  `Documentation/audits/2026-08-05-mvp-implementation-sequencing.md`. The `code` is reserved, not live:
  clients must not branch on `too_many_attempts` today.

#### CSRF mechanism (double-submit, JWT-bound)

1. On a `client_type=browser` login, the server generates a cryptographically random 32-byte token
   (`secrets.token_urlsafe(32)`).
2. That token is embedded **as a `csrf` claim inside the session JWT** and the JWT is set as the
   httpOnly cookie. It is *also* returned in the JSON body, which the SPA keeps in memory only (never
   in `localStorage`, never in a readable cookie).
3. On every `POST`/`PUT`/`PATCH`/`DELETE` authenticated **by cookie**, the SPA sends the token as an
   `X-CSRF-Token` header. The server decodes the session JWT and compares the header against the
   `csrf` claim with a constant-time comparison (`hmac.compare_digest`).
4. `GET`/`HEAD`/`OPTIONS` are exempt (no state change). Requests authenticated by
   `Authorization: Bearer` are exempt regardless of method — there is no ambient credential to forge,
   so CSRF does not apply.

Notes on this design:
- The token is stored **raw** in the JWT claim (not hashed). Hashing would add nothing: anyone who can
  read the cookie already holds the full session. Storing it raw is what lets `GET /api/v1/auth/me`
  return the token again after a page reload (see below).
- The claim lives in the signed JWT rather than in server-side state, so CSRF validation stays
  **stateless** — no Redis/sticky sessions required, consistent with `CLAUDE.md`'s stateless-services
  principle. A rotating per-request token would require shared server state and is deliberately not
  used for MVP.
- Failure modes: header missing -> `403 {"code": "csrf_token_missing"}`; header present but not equal
  to the claim -> `403 {"code": "csrf_token_invalid"}`; cookie absent/expired -> `401
  {"code": "not_authenticated"}` (checked before CSRF, so an expired session reads as 401, letting the
  SPA redirect to `/login` rather than showing a confusing 403).

#### `GET /api/v1/auth/me`

Resolves the gap flagged in `Documentation/system-design/frontend-admin-dashboard.md` §5/§7: the SPA
cannot decode the httpOnly cookie (that is the point of httpOnly) and therefore has no other way to
learn who it is logged in as.

```
GET /api/v1/auth/me
Cookie: session=<jwt>            // or: Authorization: Bearer <jwt>

Response 200:
{
  "id": "u-agent-1",
  "name": "Siti Rahayu",
  "email": "siti.agent@evdekimi.test",
  "role": "admin" | "agent" | "client",
  "status": "active",
  "session": {
    "auth_method": "cookie" | "bearer",
    "expires_at": "2026-08-05T11:00:00+07:00",
    "csrf_token": "9f2c..." | null          // non-null only when auth_method == "cookie"
  }
}
```

- **Roles:** `admin`, `agent`, `client` — any authenticated user, always scoped to *itself*. There is
  no `GET /auth/me?user_id=...` variant; the endpoint reads identity exclusively from the validated
  token, so it cannot be used to probe other accounts.
- **Returns `csrf_token`, not just identity.** A full page reload wipes the SPA's in-memory CSRF token
  while the httpOnly cookie survives, so without this the user would appear logged in but every write
  would fail with `csrf_token_missing`. Since the SPA already calls `/auth/me` once on app load, the
  token rides along on that call. This is safe for the same reason the double-submit scheme works at
  all: a cross-origin attacker cannot read this response (CORS blocks it), and a same-origin script
  could have read the login response anyway.
- **Failure modes:** no cookie and no `Authorization` header, or an expired/invalid/tampered token ->
  `401 {"code": "not_authenticated"}` (never `403` — the SPA's `apiClient` treats 401 as "redirect to
  `/login`", per the frontend design doc §5); token valid but the user was deleted or disabled since
  issuance -> `401 {"code": "session_revoked"}`, which forces re-login rather than leaving a disabled
  account with a working UI until the JWT expires.

#### `POST /api/v1/auth/logout`

Not in the original gap list, but a cookie-based session has no other termination path — the SPA
cannot clear an httpOnly cookie from JavaScript.

```
POST /api/v1/auth/logout
Cookie: session=<jwt>
X-CSRF-Token: 9f2c...

Response 204: (no body)
Set-Cookie: session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0
```

- **Roles:** any authenticated user. **Failure modes:** already logged out / no cookie -> `204`
  anyway (idempotent, so a double-click or a stale tab does not surface an error); invalid CSRF header
  -> `403 {"code": "csrf_token_invalid"}`.
- **Revokes the presented token immediately** via a Redis-backed denylist keyed on the JWT's `jti`
  claim (`app/core/revocation.py`), superseding ruling **A3**'s original "stateless, no denylist"
  MVP call once Redis entered the stack. Applies equally to cookie and Bearer sessions — a
  Bearer-authenticated caller's token also stops working right away, not just past its 60-minute TTL,
  and no cookie header is set for that caller since there is no cookie to clear. Only the one token
  presented is revoked, never every session belonging to that user. The denylist check fails open
  (see that module's docstring) if Redis is unreachable, so a Redis outage degrades to the original
  TTL-only behavior rather than blocking login/logout entirely.

### `POST /api/v1/bookings/{id}/reschedule`

Resolves the gap flagged in `Documentation/system-design/frontend-admin-dashboard.md` §7 (booking
reschedule). Conflict detection reuses `app/booking/slots.py`, the same module `schedule_viewing` and
`reschedule_viewing` call — see Design Decisions §9 for why this is a distinct operation rather than
cancel + rebook.

```
POST /api/v1/bookings/booking-001/reschedule
Cookie: session=<jwt>            // or Authorization: Bearer <jwt>
X-CSRF-Token: 9f2c...            // required for cookie-authenticated callers only

Request:
{
  "requested_slot_time": "2026-08-08T13:00:00+07:00",   // ISO-8601, timezone-aware, required
  "reason": "Client asked to move to the afternoon"      // optional, free text, max 500 chars
}

Response 200:
{
  "booking_id": "booking-001",
  "property_id": "prop-001",
  "client_id": "u-client-1",
  "agent_id": "u-agent-1",
  "slot_time": "2026-08-08T13:00:00+07:00",
  "previous_slot_time": "2026-08-08T10:00:00+07:00",
  "availability_slot_id": "avail-002",
  "status": "confirmed",
  "rescheduled_count": 1,
  "updated_at": "2026-08-05T12:00:00+07:00",
  "property_title": "Modern 2BR Apartment near MRT Extension, Jakarta Selatan",
  "client_name": "Andi Wijaya",
  "agent_name": "Siti Rahayu"
}
```

- **Required role(s):** `admin`, `agent`, `client`. **Data scoping (mirrors `schedule_viewing`'s
  re-authorization rules exactly):**

  | Role | May reschedule |
  |---|---|
  | `admin` | any booking |
  | `agent` | bookings where `booking.agent_id == user.id` (i.e. on their own listings) |
  | `client` | bookings where `booking.client_id == user.id` |

  Scoping is applied server-side from the authenticated user; the request body carries no identity
  fields at all, so there is nothing for a caller — or for the LLM on the `RescheduleViewing` path — to
  spoof. A booking outside the caller's scope returns **`404`, not `403`**, so booking IDs cannot be
  enumerated by probing for a permission error. `403` is reserved for role-level denials on endpoints
  the role may not touch at all (e.g. an `agent` calling `/api/v1/users`).
- **Behavior:** the new slot must exist in the target agent's availability and be `open`. In one
  transaction: the old availability slot is released to `open`, the new one is marked `booked`, and the
  booking row's `slot_time`/`availability_slot_id` are updated with `rescheduled_count` incremented.
  The `booking_id` never changes. `publish_booking_rescheduled` is emitted through `NotificationPort`
  after commit (non-blocking, `LogNotifier` in MVP).
- **Failure modes:**

  | Condition | Status | `code` |
  |---|---|---|
  | Booking does not exist, or is outside the caller's scope | `404` | `booking_not_found` |
  | Booking is `cancelled` | `409` | `booking_not_reschedulable` |
  | Booking's current `slot_time` is already in the past (treated as completed) | `409` | `booking_not_reschedulable` |
  | `requested_slot_time` is in the past | `422` | `slot_time_in_past` |
  | `requested_slot_time` equals the current `slot_time` | `422` | `slot_unchanged` |
  | Requested slot is not in the agent's availability | `409` | `slot_unavailable` (includes `suggested_alternatives`) |
  | Requested slot exists but is already `booked` | `409` | `booking_slot_conflict` (includes `suggested_alternatives`) |
  | Property is no longer `active` (e.g. `sold`) | `409` | `property_not_bookable` |
  | Missing/invalid CSRF header on a cookie session | `403` | `csrf_token_invalid` |

  `suggested_alternatives` is a list of up to 3 `{ availability_slot_id, slot_time }` objects for the
  same agent/property — the same shape `schedule_viewing` returns on conflict, so the SPA renders both
  with one component (frontend design doc §8).
- **Also exposed as an LLM tool.** The same capability is reachable from chat as `RescheduleViewing`
  (contract below), approved as a fifth tool by the Project Manager in
  `2026-08-05-mvp-scope-decisions-prompt-editor-reschedule-tool.md` decision #3 — superseding the
  earlier "not exposed as a tool in this pass" position recorded in
  `2026-08-05-admin-api-contract-gaps.md` decision #10. Both surfaces share `app/booking/slots.py` and
  produce identical booking state plus a single `publish_booking_rescheduled` event; they differ only
  in how the booking is identified and how errors are rendered.

### List endpoints: pagination, filtering, sorting

Resolves the gap flagged in `Documentation/system-design/frontend-admin-dashboard.md` §7 (pagination/
filter params) and the matching follow-up in the SPA architecture checkpoint. One consistent pattern
covers `GET /api/v1/properties`, `GET /api/v1/bookings`, and `GET /api/v1/users`, implemented once in
`app/api/pagination.py` so the three routers cannot drift apart.

**Common query parameters**

| Param | Type | Default | Rules |
|---|---|---|---|
| `page` | int, >= 1 | `1` | 1-based. A page beyond the end returns `200` with an empty `results` array, not `404` |
| `page_size` | int, 1..`MAX_PAGE_SIZE` | `DEFAULT_PAGE_SIZE` (20) | Above the ceiling -> `422 {"code": "page_size_too_large"}`; silently clamping would make the client believe it received the full page it asked for |
| `sort` | string | per-endpoint (below) | A field name from that endpoint's whitelist, optionally `-`-prefixed for descending (`sort=-created_at`). Unknown field -> `422 {"code": "invalid_sort_field"}` |

**Common response envelope** (identical for all three endpoints; only `results[]`'s item type differs):

```
Response 200:
{
  "results": [ ... ],
  "page": 1,
  "page_size": 20,
  "total": 15,          // total matching rows AFTER RBAC scoping and filters, not table size
  "total_pages": 1
}
```

Offset pagination (`page`/`page_size`) is chosen over cursor pagination deliberately: the admin
dashboard needs a total count and jump-to-page navigation (UI/UX doc's list screens), datasets are
small, and `total` is cheap at MVP scale. The known trade-off is that deep offsets and concurrent
inserts degrade/skew — the documented upgrade path, when a list outgrows a few thousand rows, is to
add an *optional* `cursor` param alongside `page` and let clients migrate, rather than change the
envelope shape.

**Filters are always intersected with RBAC scoping, never a substitute for it.** A filter can only
narrow what the caller may already see; passing `agent_id=<someone else>` yields an empty result set,
never another agent's data. Repeated params mean OR within one filter (`?status=active&status=draft`),
different params mean AND.

**`GET /api/v1/properties`** — item type: `PropertySummary` (same shape `search_property` returns).

| Filter | Type | Notes |
|---|---|---|
| `q` | string | Free-text over `title`, `description`, `address` |
| `city` | string, repeatable | Exact match on `city` |
| `status` | enum `active\|draft\|under_offer\|sold`, repeatable | Intersected with role scoping (below) |
| `property_type` | enum `apartment\|house\|studio\|townhouse\|villa`, repeatable | Property `property_type` field |
| `listing_type` | enum `sale\|rent` | |
| `agent_id` | string | Useful for `admin`; for an `agent` it can only ever match their own id |
| `min_price` / `max_price` | number | `min_price > max_price` -> `422 {"code": "invalid_price_range"}` |
| `bedrooms` | int | Minimum bedrooms (`>=`) |

Sort whitelist: `listed_date` (default `-listed_date`), `price`, `title`, `city`.
RBAC scoping: `client` -> `status="active"` only; `agent` -> all `active` plus their own `draft`/
`under_offer`/`sold`; `admin` -> unrestricted. Identical to `search_property`'s scoping so the chat
and dashboard surfaces cannot disagree about visibility — both consume one shared scoped-query builder
rather than two implementations kept in sync by convention. Geo filters (`near_lat`/`near_lng`/
`radius_km`) stay tool-only for MVP — the dashboard has no map view; adding them here later is
additive and breaks nothing.

> **Naming note — `property_type`, never bare `type`.** The property-type field is named
> `property_type` uniformly: on the `Property` model, in `backend/seed_data/properties.json`, on
> `PropertySummary`, as this filter's query param, and as `SearchPropertyInput.property_type`. The
> earlier split (`type` in the model/REST filter vs. `property_type` in the tool schema, recorded in
> `Documentation/audits/2026-08-05-admin-api-contract-gaps.md`) is **superseded** — see
> `Documentation/audits/2026-08-05-escalation-assignment-contract.md` § Section 2 for the decision and
> rationale. The chief reason: `PropertySummary` is the item type of both the tool output and this
> endpoint, so under the old split the model would filter with one name and read results back under
> another, inside the same context window. Nothing in the codebase should use bare `type`.

**`GET /api/v1/bookings`** — item type: booking object (same fields as the reschedule response, minus
`previous_slot_time`, which only means anything on a move):

```
{
  "booking_id": "booking-001",
  "property_id": "prop-001",
  "client_id": "u-client-1",
  "agent_id": "u-agent-1",
  "slot_time": "2026-08-08T10:00:00+07:00",
  "availability_slot_id": "avail-001",
  "status": "confirmed",
  "rescheduled_count": 0,
  "updated_at": "2026-08-05T09:12:00+07:00",
  "property_title": "Modern 2BR Apartment near MRT Extension, Jakarta Selatan",
  "client_name": "Andi Wijaya",
  "agent_name": "Siti Rahayu"
}
```

| Filter | Type | Notes |
|---|---|---|
| `property_id` | string, repeatable | |
| `agent_id` | string | Admin-useful; an `agent` can only match their own id |
| `client_id` | string | Admin/agent-useful; a `client` can only match their own id |
| `status` | enum `confirmed\|cancelled\|completed`, repeatable | |
| `date_from` / `date_to` | ISO-8601 datetime | Inclusive range over `slot_time`; `date_from > date_to` -> `422 {"code": "invalid_date_range"}` |

Sort whitelist: `slot_time` (default `-slot_time`), `created_at`, `status`.
RBAC scoping: `admin` -> all; `agent` -> `agent_id == user.id`; `client` -> `client_id == user.id`.
This is the same scoping `reschedule_viewing` resolves bookings within, so what a user can see in the
dashboard and what they can reschedule in chat are the same set by construction.

> **`property_title`, `client_name`, and `agent_name` are denormalized onto every booking response**
> (`Documentation/audits/2026-08-06-booking-response-name-denormalization.md`) — read at query time from
> the referenced `Property`/`User` rows, never stored on the booking. The booking screens render names,
> and `GET /api/v1/users/{id}` is admin-only, so an `agent` or `client` has no other way to resolve the
> three ids; the frontend must not attempt a client-side join. All three are non-nullable (the ids are
> required foreign keys) and appear on every endpoint returning a booking object: this list,
> `GET /{id}`, `POST /{id}/cancel`, and `POST /{id}/reschedule`.

**`GET /api/v1/users`** — item type: user object (`id`, `name`, `email`, `role`, `status`,
`created_at`). Never includes `hashed_password`.

| Filter | Type | Notes |
|---|---|---|
| `q` | string | Free-text over `name`, `email` |
| `role` | enum `admin\|agent\|client`, repeatable | |
| `status` | enum `active\|disabled`, repeatable | |

Sort whitelist: `name` (default), `email`, `role`, `created_at`.
RBAC: `admin` only — `agent` and `client` receive `403 {"code": "forbidden"}` (role-level denial, so
`403` rather than the `404` used for out-of-scope individual records).

> **Data-model addition required:** the seeded `User` has no `status` or `created_at` field. Both are
> needed here — the dashboard deactivates users rather than deleting them (deletion would orphan
> bookings), and `created_at` is the natural default sort for a growing user list. Backend Engineer:
> add `status: "active" | "disabled"` (default `active`) and `created_at` to `app/models/user.py` and
> to `backend/seed_data/users.json`. `login` rejects `status="disabled"` with `403 account_disabled`,
> and `/auth/me` returns `401 session_revoked` for a user disabled mid-session. `status` is also load
> bearing for `escalate_to_human`'s listing-agent assignment, which skips disabled agents.

### Single-item reads: `GET /{id}`

Each list endpoint has a matching single-item read, backing the UI/UX doc's detail screens (§5.3
`/properties/:id`, §5.5 `/bookings/:id`). Specified in
`Documentation/audits/2026-08-06-single-item-get-endpoints.md`.

| Endpoint | Returns | Roles | Resolver |
|---|---|---|---|
| `GET /api/v1/properties/{id}` | `PropertyDetail` | `admin`, `agent`, `client` | `find_visible_property` |
| `GET /api/v1/bookings/{id}` | booking object (same shape as the list item) | `admin`, `agent`, `client` | `scoped_booking_query` |
| `GET /api/v1/users/{id}` | user object (same shape as the list item) | `admin` only | `db.get` — users have no per-record scope |

**Response shapes reuse the existing models; there is no `BookingDetail` or `UserDetail`.** Bookings
and users have no summary/detail split — their list item type is already the complete record, so the
single-GET returns exactly what the corresponding list row contains. Properties *does* have a split,
and the single-GET returns the **detail** shape, matching `POST`/`PUT`/`DELETE`.

> **`PropertySummary` is not a substitute for `PropertyDetail`.** It omits `latitude`, `longitude`,
> `amenities`, `description`, and `listed_date` — all of which `PUT /api/v1/properties/{id}` accepts as
> a *full replacement*. An edit form hydrated from a cached list row would blank `description` and
> `amenities` on save and would have no coordinates to submit at all. The detail screen must fetch
> `GET /api/v1/properties/{id}`, not reuse the row it was clicked from.

**Read scope is the *visibility* scope, not the *editable* scope.** `GET /api/v1/properties/{id}`
resolves through the same rule as `GET /api/v1/properties`: a `client` sees `active` listings only, an
`agent` sees every `active` listing plus their own in any status, an `admin` is unrestricted. So an
agent may open the detail screen for another agent's `active` listing read-only — the same rows their
list screen already shows them — while `PUT`/`DELETE` continue to resolve through the narrower
`editable_property_query` and answer `404` for a listing they do not own. The SPA renders
Save/Deactivate when `user.role === "admin" || property.agent_id === user.id`; there is no `can_edit`
field, because it is derivable and the backend re-enforces on the write regardless.

Bookings have no such split: the scope that governs `GET /api/v1/bookings/{id}` is the same one that
governs cancel and reschedule (`admin` any; `agent` where `agent_id == user.id`; `client` where
`client_id == user.id`).

**Failure modes:**

| Condition | Status | `code` |
|---|---|---|
| Property id unknown, or outside the caller's visibility scope | `404` | `property_not_found` |
| Booking id unknown, or outside the caller's scope | `404` | `booking_not_found` |
| `GET /users/{id}` called by an `agent` or `client` | `403` | `forbidden` |
| `GET /users/{id}` by an `admin`, id unknown | `404` | `user_not_found` |

Same posture as everywhere else: an out-of-scope *individual record* is indistinguishable from a
nonexistent one, so ids cannot be enumerated by probing for a permission error. `403` is reserved for
role-level denial of an endpoint the role may not touch at all — which is why `/users/{id}` is the only
one of the three with a `403` path, exactly as `GET /users` already is. A `403` on
`GET /api/v1/properties/{id}` would confirm that a `draft` or `sold` listing exists, which no other
endpoint reveals. `GET` is CSRF-exempt, so no `csrf_token_invalid` applies.

### Tool contracts (illustrative — Pydantic AI tool signatures)

```python
# search_faq — RAG lookup, no write, no identity scoping
class SearchFaqInput(BaseModel):
    query: str
    top_k: int = 3          # bounded by RAG_TOP_K server-side, LLM value is a hint not a ceiling

class FaqHit(BaseModel):
    id: str
    question: str
    answer: str
    category: str
    score: float

class SearchFaqOutput(BaseModel):
    results: list[FaqHit]   # empty list, not an error, when nothing clears RAG_MIN_SCORE
```
Roles: admin, agent, client. Failure modes: embedding provider unreachable -> `ToolError` caught by
orchestrator, surfaced as an apology + `EscalateToHuman` suggestion; empty index -> empty `results`
(not an error).

```python
# search_property (exposed as "SearchProperty")
class SearchPropertyInput(BaseModel):
    city: str | None = None
    listing_type: Literal["sale", "rent"] | None = None
    property_type: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    bedrooms: int | None = None
    near_lat: float | None = None
    near_lng: float | None = None
    radius_km: float | None = None
    keywords: str | None = None
    limit: int = 10

class SearchPropertyOutput(BaseModel):
    results: list[PropertySummary]
    count: int
```
Roles: admin, agent, client. Data scoping: `client` always sees `status="active"` only; `agent` also
sees their own `draft`/`under_offer` listings; `admin` unrestricted — applied through the shared
RBAC-scoped property query builder, the same one `GET /api/v1/properties` and `escalate_to_human`'s
assignment lookup use. `property_type` is the same field name carried by `PropertySummary`, the
`Property` model, and the REST filter (see the naming note above), so the model filters and reads
results using one vocabulary. Failure modes: no results -> empty list (not an error); DB unavailable ->
`ToolError` -> orchestrator apology + `EscalateToHuman` suggestion.

```python
# schedule_viewing (exposed as "BookViewing")
class ScheduleViewingInput(BaseModel):
    property_id: str
    requested_slot_time: datetime
    client_id: str | None = None   # only honored for agent/admin callers, see RBAC re-auth above

class ScheduleViewingOutput(BaseModel):
    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    status: Literal["confirmed"]
```
Roles: admin, agent, client (self only). Failure modes: property not found (`404`-equivalent
`ToolError`); slot not in agent availability / already booked (`409`-equivalent `ToolError` with
`suggested_alternatives`); property not active (`sold`/`draft`) -> validation error; past
`requested_slot_time` -> validation error.

```python
# reschedule_viewing (exposed as "RescheduleViewing")
class RescheduleViewingInput(BaseModel):
    requested_slot_time: datetime               # the new slot; ISO-8601, timezone-aware
    booking_id: str | None = None               # preferred, when the conversation already surfaced it
    property_id: str | None = None              # fallback identifier: which listing's viewing to move
    current_slot_time: datetime | None = None   # fallback identifier: which existing slot to move
    reason: str | None = None                   # optional, max 500 chars, carried into the event

class RescheduleViewingOutput(BaseModel):
    booking_id: str            # unchanged by a reschedule — same id as before
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime        # the new slot
    previous_slot_time: datetime
    status: Literal["confirmed"]
    rescheduled_count: int
```
Roles: admin, agent, client. Moves an existing booking to a different slot on the **same property with
the same agent**, keeping the same `booking_id` — it is not cancel-then-rebook (Design Decisions §9),
and it cannot change *which* property is being viewed (that is a `BookViewing` plus a cancel).
Availability resolution and conflict detection call `app/booking/slots.py`, the same module
`BookViewing` and the REST reschedule endpoint call; this tool adds booking resolution and RBAC
re-authorization on top of it, never a second conflict implementation.

**Booking resolution** (all three identifying fields are optional — the LLM rarely knows an internal
`booking_id`, and the tool set is closed at five, so there is no lookup tool to call first). Within the
caller's RBAC scope only: an explicit `booking_id` is looked up directly; otherwise the caller's
`confirmed`, future bookings are filtered by whichever of `property_id`/`current_slot_time` were
supplied, or returned outright if neither was given. Exactly one match proceeds; zero matches ->
`booking_not_found`; more than one -> `booking_ambiguous`. The resolver *is* the authorization
boundary — it never searches outside `ctx.deps.user`'s scope, so an out-of-scope `booking_id` is
indistinguishable from a nonexistent one, same as the REST endpoint (this matters more here since the
ID is LLM-generated and a distinguishable "forbidden" would let a user enumerate booking IDs via chat).

**Failure modes** (raised as `ToolError`s, not surfaced raw): `booking_not_found` (no match in scope,
incl. out-of-scope `booking_id`) -> orchestrator apologizes, may suggest `EscalateToHuman`;
`booking_ambiguous` (+ `candidates: [{booking_id, property_title, slot_time}]`, capped at 5, no client
names/emails/contact details) -> orchestrator **must ask the user which booking they mean**;
`booking_not_reschedulable` (cancelled or already-past booking); `slot_time_in_past` /
`slot_unchanged` -> validation error; `slot_unavailable` / `booking_slot_conflict` (+
`suggested_alternatives`, same shape `BookViewing` returns).

> **Safety-critical, not optional:** on `booking_ambiguous`, the model must ask the user to disambiguate
> and must **never** auto-select a candidate (e.g. retry with `candidates[0]`) — doing so would silently
> reschedule the wrong person's viewing. This has to be enforced via the tool description and system
> prompt text, **not** via code that inspects the model's next tool call, which would be exactly the
> pre-classification routing layer `CLAUDE.md` forbids as a hard constraint. Whoever authors the system
> prompt owns this — see `Documentation/audits/2026-08-05-reschedule-viewing-tool-contract.md` decision
> #8 for the full reasoning and the QA assertion this needs.

**Booking identification — stated ambiguity and its assumed resolution.** A chat user says "move my
Saturday viewing to Monday afternoon." They do not know internal booking IDs, and the tool set is
closed at five, so there is no `list_my_bookings` tool for the model to call first. **Resolution: all
three identifying fields are optional, and the tool resolves the booking server-side** against the
caller's RBAC-scoped booking set:

| Input given | Behavior |
|---|---|
| `booking_id` | Looked up **within the caller's scope only**. Nonexistent or out of scope -> `booking_not_found` |
| No `booking_id`, but `property_id` and/or `current_slot_time` | Filter the caller's `confirmed`, future bookings by whichever fields were supplied |
| Nothing | Filter to the caller's `confirmed`, future bookings — for a `client` with one upcoming viewing this resolves cleanly, which is the common case |
| Filter matches exactly one | Proceed with the reschedule |
| Filter matches more than one | `booking_ambiguous` `ToolError` carrying `candidates: [{ booking_id, property_title, slot_time }]` (max 5), so the LLM asks the user which one and calls again with the chosen `booking_id` |
| Filter matches none | `booking_not_found` `ToolError` |

The resolver **is** the authorization boundary: it never searches outside `ctx.deps.user`'s scope, so
an out-of-scope `booking_id` is indistinguishable from a nonexistent one — the same `404`-not-`403`
posture the REST endpoint takes, for the same reason (no ID enumeration, including via the LLM).
Rejected alternative: make `booking_id` required and add a sixth booking-lookup tool — that spends a
tool slot the Project Manager explicitly closed
(`2026-08-05-mvp-scope-decisions-prompt-editor-reschedule-tool.md` decision #5) and adds a round trip
to every reschedule. `agent`/`admin` callers, whose scope is wide, will in practice hit
`booking_ambiguous` unless they supply `booking_id`; that is intended behavior, not a degradation.

**RBAC re-authorization** (mirrors `schedule_viewing`'s pattern exactly, per Design Decisions §5): the
input schema deliberately carries **no identity fields at all** — no `client_id`, no `agent_id`.
Unlike `BookViewing`, there is nothing for an `agent`/`admin` to supply on a client's behalf, because
the booking being moved already carries its own `client_id`/`agent_id`. The searchable scope is derived
from `ctx.deps.user` server-side, never from LLM-supplied arguments:

| Caller role | Resolvable booking set |
|---|---|
| `admin` | any booking |
| `agent` | bookings where `booking.agent_id == ctx.deps.user.id` |
| `client` | bookings where `booking.client_id == ctx.deps.user.id` |

So a `client` cannot reschedule another user's viewing no matter what `booking_id` the model generates,
and an `agent` cannot touch another agent's bookings — the tool re-checks rather than trusting the LLM,
per `CLAUDE.md`'s "tool calls are re-authorized, not trusted."

Failure modes use the REST endpoint's taxonomy, raised as `ToolError`s the orchestrator turns into a
coherent reply rather than surfacing raw:

| Condition | `ToolError` code | REST equivalent | Orchestrator recovery |
|---|---|---|---|
| No booking matched in the caller's scope | `booking_not_found` | `404` | Say no matching viewing was found; offer `SearchProperty`/`BookViewing` or `EscalateToHuman` |
| More than one booking matched | `booking_ambiguous` (+ `candidates`) | n/a — tool-only | Ask the user which viewing, then retry with the chosen `booking_id` |
| Booking is `cancelled`, or its current slot is already past | `booking_not_reschedulable` | `409` | Explain it cannot be moved; offer to book a new viewing |
| `requested_slot_time` is in the past | `slot_time_in_past` | `422` | Ask for a future time |
| `requested_slot_time` equals the current slot | `slot_unchanged` | `422` | Confirm the booking is already at that time; no write occurs |
| Requested slot is not in the agent's availability | `slot_unavailable` (+ `suggested_alternatives`) | `409` | Offer the alternatives |
| Requested slot exists but is already `booked` | `booking_slot_conflict` (+ `suggested_alternatives`) | `409` | Offer the alternatives |
| Property is no longer `active` (e.g. `sold`) | `property_not_bookable` | `409` | Explain, and offer `EscalateToHuman` |
| DB/transaction failure | generic `ToolError` | `500` | Apologize and suggest `EscalateToHuman`; the booking is unchanged, since the slot swap is one transaction |

`suggested_alternatives` uses the same `{ availability_slot_id, slot_time }` shape (max 3) that
`BookViewing` and the REST endpoint return, so all three surfaces describe a conflict identically.
`availability_slot_id` and `updated_at` appear in the REST response but are **omitted from the tool
output** — they are internal bookkeeping the model has no use for, and every extra field is more
surface for it to mention or hallucinate about. `publish_booking_rescheduled` is emitted after commit
through `NotificationPort`, exactly as on the REST path, so a chat-initiated and a dashboard-initiated
reschedule are indistinguishable downstream.

```python
# escalate_to_human (exposed as "EscalateToHuman")
class EscalateToHumanInput(BaseModel):
    reason: str
    category: Literal["complaint", "complex_request", "policy_exception", "technical_issue", "other"] = "other"
    conversation_summary: str
    urgency: Literal["low", "medium", "high"] = "medium"
    property_id: str | None = None

class EscalateToHumanOutput(BaseModel):
    escalation_id: str | None  # null only on the degraded double-persistence-failure path, see below
    status: Literal["queued", "queued_unassigned"]
    assigned_agent_id: str | None
    message: str
```
Roles: admin, agent, client — always allowed as a safety valve, always tied to the caller's own
identity/conversation (cannot escalate on behalf of another user).

**`escalation_id` is nullable.** This **supersedes** the originally frozen `str` — forced by the
already-documented failure mode below (persistence failure → one retry → a static support-contact
fallback reply): on that degraded path nothing was ever persisted, so there is no id to return, and the
schema needs to say so rather than lie. Every successful call still returns a real id; the null case is
exactly the fallback path, nothing else.

**Listing-agent assignment.** When the escalation carries a `property_id` that resolves to a property
with an active listing agent, `assigned_agent_id` is set to that agent and `status` is `"queued"`;
otherwise `assigned_agent_id` is `null` and `status` is `"queued_unassigned"`. This **supersedes** the
earlier "no agent assignment logic exists, so `assigned_agent_id` is always `null`" behavior — see
`Documentation/audits/2026-08-05-pm-signoffs-model-routing-and-escalation-assignment.md` Decision 2 for
the approval and
`Documentation/audits/2026-08-05-escalation-assignment-contract.md` § Section 1 for the full edge-case
contract.

> **This is an assignment *field*, not a human handoff.** No human is paged, no support queue or CRM is
> consumed, no SLA exists, and the assigned agent has no UI in which to see the escalation. Assignment
> makes the record *queryable* ("escalations on my listings") the moment someone builds that screen; it
> does not make anyone respond. The user-facing `message` must correspondingly not promise a callback —
> `"I've logged this and flagged it to the agent handling that listing. Reference: <id>."`, never "an
> agent will contact you shortly."

**Assignment resolution.** Assignment **never fails the tool** — every negative branch degrades to the
unassigned path, because `EscalateToHuman` is the fallback every other tool relies on and must not
acquire a new way to break:

| Condition | Result |
|---|---|
| No `property_id` supplied | `queued_unassigned`, `assigned_agent_id = null` |
| `property_id` does not exist, **or** resolves outside the caller's RBAC-scoped property set | `queued_unassigned` — the two cases are deliberately indistinguishable |
| Property has no `agent_id` | `queued_unassigned` |
| Listing agent is `disabled`, or no longer holds an `agent`/`admin` role | `queued_unassigned` |
| Listing agent is active — **including when the caller is that agent** (self-assignment) | `queued`, `assigned_agent_id = property.agent_id` |

The property lookup uses the **same RBAC-scoped property query** `SearchProperty` and
`GET /api/v1/properties` use, not a raw primary-key read. Since `property_id` is LLM-supplied, a bare
lookup would let a `client` probe for the existence and owner of a `draft`/`sold` listing by watching
whether an assignee comes back — the same enumeration oracle the `404`-not-`403` booking posture closes.
Only a *resolved* property id is persisted on the escalation row; an unresolvable one is stored as
`null` and recorded in a structured warning log, so "escalations for property X" queries stay correct.

Failure modes: persistence failure -> retried once, then falls back to a static "please contact
support@..." reply so the user is never left without a next step; repeated escalations from the same
session are rate-limited to avoid trivial abuse. `publish_escalation_created` is emitted after commit
and carries `assigned_agent_id` and `status` (but not `reason`/`conversation_summary` — see Design
Decisions §8).

### Tool RBAC summary

| Tool (exposed name) | admin | agent | client | Scoping mechanism |
|---|---|---|---|---|
| `search_faq` | yes | yes | yes | none — public knowledge |
| `SearchProperty` | yes, unrestricted | yes, own `draft`/`under_offer` + all `active` | yes, `active` only | server-enforced from `ctx.deps.user`, not LLM args |
| `BookViewing` | yes, any `client_id` | yes, own properties, valid `client_id` | yes, self only (enforced override) | `client_id` re-authorized server-side |
| `RescheduleViewing` | yes, any booking | yes, own `agent_id` bookings | yes, own `client_id` bookings | booking resolved only within the caller's scope; no identity fields in the schema |
| `EscalateToHuman` | yes, own session | yes, own session | yes, own session | tied to caller identity/`conversation_id`; assignment lookup uses the caller's property scope |

## Architecture (MVP as built)

```mermaid
flowchart TB
    Client["Client (chat consumer — web/mobile)"]
    AdminSPA["Admin Dashboard<br/>(frontend/admin — separate React SPA, own deploy)"]
    LLM["External LLM API<br/>(OpenAI / Anthropic / Gemini — via LLM_PROVIDER)"]
    EMB["External/local Embedding model<br/>(via EMBEDDING_PROVIDER)"]
    DB[("SQLite / PostgreSQL, via SQLModel")]
    Seed["backend/seed_data/*.json"]
    Log[("Structured app logs")]

    subgraph App["FastAPI Application — single process, modular monolith"]
        Routers["app/api<br/>(auth, chat, properties, bookings, users)"]
        Orchestrator["app/agent<br/>(Pydantic AI Agent + 5 tools + provider factory)"]
        Slots["app/booking/slots.py<br/>(availability + conflict detection)"]
        RAG["app/rag<br/>(in-process vector index)"]
        Core["app/core<br/>(config, JWT/RBAC/CSRF, logging)"]
        Notif["app/notifications<br/>(NotificationPort: LogNotifier)"]
    end

    Client -->|HTTPS + Bearer JWT| Routers
    AdminSPA -->|HTTPS + httpOnly cookie + X-CSRF-Token| Routers
    Routers --> Core
    Routers -->|message + AuthenticatedUser| Orchestrator
    Routers -->|reschedule / cancel| Slots
    Orchestrator -->|tool: search_faq| RAG
    Orchestrator -->|tools: SearchProperty, BookViewing,<br/>RescheduleViewing, EscalateToHuman<br/>re-authorized via app/core| Routers
    Orchestrator -->|BookViewing / RescheduleViewing<br/>conflict check| Slots
    Orchestrator -->|chat/completion calls| LLM
    RAG -->|embedding calls| EMB
    Routers --> DB
    Slots --> DB
    RAG --> DB
    DB -.->|loaded once, empty DB only| Seed
    Routers --> Notif
    Notif -.->|MVP: structured log line| Log
```

The admin dashboard is deployed and scaled independently of this backend (static hosting/CDN) — it's
just another authenticated client of `app/api`, same as the chat-consuming client. See
`Documentation/system-design/frontend-admin-dashboard.md` for its internal architecture.

## Booking Flow (`BookViewing` / `schedule_viewing`)

```mermaid
sequenceDiagram
    actor U as Client User
    participant API as app/api/chat.py
    participant Core as app/core (JWT/RBAC)
    participant Orch as app/agent (Pydantic AI Agent)
    participant Tool as schedule_viewing tool
    participant DB as Booking data (SQLModel)
    participant Notif as NotificationPort (LogNotifier)

    U->>API: POST /chat/messages "Book the 2BR in Jakarta Selatan this Saturday" (JWT)
    API->>Core: validate JWT -> AuthenticatedUser(role=client, id=u-client-1)
    Core-->>API: AuthenticatedUser
    API->>Orch: run(message, deps=AgentDeps(user, db, rag))
    Orch->>Orch: LLM selects BookViewing via native function-calling (no if/else)
    Orch->>Tool: schedule_viewing(property_id, requested_slot_time, client_id?)
    Tool->>Tool: RE-AUTHORIZE: override client_id with ctx.deps.user.id (role=client)
    Tool->>DB: check property.status=="active" AND slot in agent availability, not already booked
    alt slot available and property active
        Tool->>DB: insert booking (status=confirmed)
        DB-->>Tool: booking_id
        Tool->>Notif: publish_booking_created (async, non-blocking)
        Tool-->>Orch: ScheduleViewingOutput(confirmed)
        Orch-->>API: grounded reply
        API-->>U: "Your viewing is booked for Saturday at 10:00 with Siti..."
    else slot already booked (conflict)
        Tool-->>Orch: ToolError(409, suggested_alternatives=[...])
        Orch-->>API: reply with alternative times
        API-->>U: "That slot's taken — here are other times: ..."
    else property not found / not active
        Tool-->>Orch: ToolError(404 / invalid_state)
        Orch-->>API: apology, may call EscalateToHuman next turn
        API-->>U: "I couldn't book that listing right now..."
    end
```

## Reschedule Flows

The same state transition is reachable from two surfaces. Both call `app/booking/slots.py` and emit
one `publish_booking_rescheduled` event; they differ only in how the booking is identified and how
errors are rendered (Design Decisions §9).

### Dashboard-initiated (`POST /api/v1/bookings/{id}/reschedule`)

No LLM is involved — a direct REST call from the admin SPA, with the booking identified by a path
parameter.

```mermaid
sequenceDiagram
    actor S as Admin/Agent (admin SPA)
    participant API as app/api/bookings.py
    participant Core as app/core (cookie session + CSRF)
    participant Slots as app/booking/slots.py
    participant DB as Booking data (SQLModel)
    participant Notif as NotificationPort (LogNotifier)

    S->>API: POST /bookings/booking-001/reschedule (cookie + X-CSRF-Token)
    API->>Core: validate session JWT, compare X-CSRF-Token to `csrf` claim
    alt CSRF invalid / session expired
        Core-->>API: reject
        API-->>S: 403 csrf_token_invalid / 401 not_authenticated
    else authenticated
        Core-->>API: AuthenticatedUser(role, id)
        API->>DB: load booking scoped by role (agent -> own agent_id, client -> own client_id)
        alt not found or out of caller's scope
            API-->>S: 404 booking_not_found (never 403 — no ID enumeration)
        else found and reschedulable
            API->>Slots: resolve_slot(agent_id, property_id, requested_slot_time)
            alt slot open
                Slots->>DB: TX: release old slot -> claim new slot -> update booking
                DB-->>API: updated booking (same booking_id, rescheduled_count+1)
                API->>Notif: publish_booking_rescheduled (after commit, non-blocking)
                API-->>S: 200 booking with previous_slot_time
            else slot booked / not in availability
                Slots-->>API: conflict + up to 3 alternatives
                API-->>S: 409 booking_slot_conflict / slot_unavailable + suggested_alternatives
            end
        end
    end
```

### Chat-initiated (`RescheduleViewing` tool)

The user never supplies a booking ID. The extra step versus the REST path is **booking resolution**,
which doubles as the authorization boundary — it only ever searches the caller's own scope.

```mermaid
sequenceDiagram
    actor U as Client User
    participant API as app/api/chat.py
    participant Orch as app/agent (Pydantic AI Agent)
    participant Tool as reschedule_viewing tool
    participant Slots as app/booking/slots.py
    participant DB as Booking data (SQLModel)
    participant Notif as NotificationPort (LogNotifier)

    U->>API: POST /chat/messages "Move my Saturday viewing to Monday afternoon"
    API->>Orch: run(message, deps=AgentDeps(user=client u-client-1, db, rag))
    Orch->>Orch: LLM selects RescheduleViewing (not BookViewing) from context
    Orch->>Tool: reschedule_viewing(requested_slot_time, booking_id?, property_id?, current_slot_time?)
    Tool->>DB: RESOLVE within ctx.deps.user's scope only (client -> own client_id)
    alt no booking matched
        Tool-->>Orch: ToolError(booking_not_found)
        Orch-->>U: "I couldn't find an upcoming viewing to move — want me to book one?"
    else more than one matched
        Tool-->>Orch: ToolError(booking_ambiguous, candidates=[...])
        Orch-->>U: "You have two upcoming viewings — the Kemang unit or the Senopati one?"
        U->>API: "The Kemang one"
        Note over Orch,Tool: LLM calls the tool again with the chosen booking_id
    else exactly one matched and reschedulable
        Tool->>Slots: resolve_slot(agent_id, property_id, requested_slot_time)
        alt slot open
            Slots->>DB: TX: release old slot -> claim new slot -> update booking
            Tool->>Notif: publish_booking_rescheduled (after commit, non-blocking)
            Tool-->>Orch: RescheduleViewingOutput(previous_slot_time, slot_time, rescheduled_count)
            Orch-->>U: "Moved — Monday 13:00 instead of Saturday 10:00, same booking."
        else slot booked / not in availability
            Slots-->>Tool: conflict + up to 3 alternatives
            Tool-->>Orch: ToolError(booking_slot_conflict, suggested_alternatives=[...])
            Orch-->>U: "Monday afternoon is taken — would 15:00 or Tuesday 10:00 work?"
        end
    end
```

## FAQ Flow (`search_faq`)

```mermaid
sequenceDiagram
    actor U as User (any role)
    participant API as app/api/chat.py
    participant Core as app/core (JWT/RBAC)
    participant Orch as app/agent (Pydantic AI Agent)
    participant Tool as search_faq tool
    participant RAG as app/rag (vector index)
    participant LLM as External LLM API

    U->>API: POST /chat/messages "What documents do I need to rent a unit?"
    API->>Core: validate JWT -> AuthenticatedUser
    Core-->>API: AuthenticatedUser
    API->>Orch: run(message, deps=AgentDeps(...))
    Orch->>LLM: turn 1 (tools declared in schema)
    LLM-->>Orch: tool_call search_faq(query="documents required to rent")
    Orch->>Tool: search_faq(query, top_k)
    Tool->>RAG: embed(query) + cosine similarity search
    RAG-->>Tool: top-k FAQ chunks with scores
    Tool-->>Orch: SearchFaqOutput(results)
    alt best score >= RAG_MIN_SCORE
        Orch->>LLM: turn 2 (tool result as grounding context)
        LLM-->>Orch: grounded natural-language answer
        Orch-->>API: reply (citing FAQ category)
        API-->>U: answer
    else no result clears RAG_MIN_SCORE
        Orch-->>API: "I'm not certain — want me to connect you with a human agent?"
        API-->>U: reply suggesting EscalateToHuman
    end
```

## Scaling to 100x

This section is prep material for the required scaling explanation video, not a replacement for it.
The full target architecture and its rationale live in `../core components.md` and the
`Documentation/audits/2026-08-04-initial-architecture-checkpoint.md` checkpoint; this MVP's module
boundaries (see the mapping table in Design Decisions §1) are chosen so that scaling is an
**extraction and infra-swap exercise, not a rewrite**:

1. **Split the LLM-bound path from the CRUD-bound path first.** `app/agent/*` and `app/api/chat.py`
   become the Agent Orchestrator and Chat API Service — these need to scale on LLM API latency and
   concurrent conversations, independently of property/booking write traffic. Session state moves out
   of request scope into Redis so any instance can serve any conversation (already designed
   stateless in the MVP; Redis just becomes the shared store instead of an in-process/DB lookup).
   The CSRF design is stateless for the same reason — the token lives in the signed session JWT, not
   in per-instance memory, so adding instances behind a load balancer needs no sticky sessions.
2. **Move property search off the transactional path.** `search_property` today queries the same
   SQL tables `app/api/properties.py` writes to. At scale this becomes CQRS: Property Service keeps
   Postgres+PostGIS as the write source of truth, and a Search Service indexes into Elasticsearch via
   CDC/outbox, exactly as designed in `core components.md` §2 (Search Service) and §3 (CQRS rationale).
   `GET /api/v1/properties`'s offset pagination is the one contract detail that needs revisiting here
   — deep offsets over an Elasticsearch read model should migrate to `search_after`/cursor paging (see
   the list-endpoint section's documented upgrade path).
3. **Move the FAQ index out of-process.** `app/rag` becomes a Knowledge Base service backed by
   pgvector/a dedicated vector DB, computing embeddings once centrally instead of per-instance.
4. **Decouple booking side-effects for real.** `LogNotifier` is swapped for a Kafka/RabbitMQ/SQS
   publisher and a standalone Notification Service consumer — the `schedule_viewing` and
   `reschedule_viewing` tools' code and the reschedule endpoint's code do not change, only the
   `NotificationPort` binding. Booking-conflict handling also concentrates in one module
   (`app/booking/slots.py`), so the row-level locking / unique-constraint work that concurrent booking
   volume will eventually demand lands in one place rather than three.
5. **Centralize auth.** `app/core/security.py` + `app/api/auth.py` become the Auth Service issuing
   JWTs that every extracted service validates independently — RBAC re-authorization at the tool/
   service layer (already enforced in the MVP, see Design Decisions §5) carries over unchanged; it was
   never dependent on being in-process. Per-IP/per-email login rate limiting (`429 too_many_attempts`,
   deliberately absent from the MVP — see the login contract) lands at the API Gateway in this step,
   which is the only layer that can hold the shared counter state it requires.
6. **Add the observability and infra layer.** OpenTelemetry tracing across the (now real) network
   hops, Prometheus/Grafana, and horizontal autoscaling per service, per `core components.md` §2
   (Observability Stack). The MVP's structured logging in `app/core/logging.py` is the seam this
   plugs into.

**Model routing** (cheap model for FAQ-style turns, stronger model for multi-step reasoning) is the
target-state cost control that sits alongside these six, deliberately unbuilt in the MVP. Its trigger
is LLM spend becoming a material line item — not traffic volume as such — and its implementation is
gated on one constraint recorded in
`Documentation/audits/2026-08-05-pm-signoffs-model-routing-and-escalation-assignment.md`: routing may
key on non-lexical signals only, never on message content, because a content classifier is one reuse
away from becoming the pre-classification tool router `CLAUDE.md` forbids outright.

Each step is independently deployable and reversible — nothing in the MVP module design requires all
six steps to happen together, which matters for an incremental rollout under real traffic growth
rather than a big-bang rewrite.

## Seed Data

Located in `backend/seed_data/`, IDs are consistent across files so bookings/properties can reference
users and each other:

| File | Contents |
|---|---|
| `users.json` | ~8 seed users across all three roles (1 admin, 3 agents, 4 clients). `hashed_password` is a **placeholder string**, not a real bcrypt hash — the Backend Engineer should replace seed values with real hashes (e.g. via `passlib`) as part of implementing `app/core/security.py`; suggested dev convention is that every seed user's plaintext password is `ChangeMe123!` once hashing lands, purely for local testing. Needs `status` and `created_at` fields added, see the `GET /api/v1/users` contract. |
| `properties.json` | 15 listings across 10 Indonesian cities, mixed `sale`/`rent`, mixed types (apartment/house/studio/townhouse/villa). Includes one `draft`, one `sold`, one `under_offer` listing specifically to exercise the `search_property` RBAC/status-scoping rules described in Design Decisions §5. **The property-type key is `property_type`** — the seed file currently uses `type` and must be renamed as part of the seed-data corrections, per the naming note under `GET /api/v1/properties`. |
| `faq.json` | 14 FAQ entries (required documents, deposit policy, pet policy, cancellation/rescheduling, viewing hours, application process, fees, lease terms, maintenance responsibility, utilities, early termination, guarantor/co-signer, payment methods, furnished vs. unfurnished) — ready to embed as-is for `app/rag`. |
| `agent_availability.json` | 10 viewing slots across the three seed agents/several properties; two are pre-marked `booked` and correspond 1:1 to confirmed entries in `viewings.json`, so `schedule_viewing`'s conflict-detection path is testable immediately without creating new data. `avail-002` (same agent `u-agent-1` and property `prop-001` as the booked `avail-001`, status `open`, 13:00) is the natural target for exercising the reschedule happy path on both the REST and `RescheduleViewing` surfaces. |
| `viewings.json` | 3 sample bookings (2 `confirmed`, matching the `booked` availability slots; 1 `cancelled`, matching a slot that is `open` again) — covers both the happy path and the "slot freed up after cancellation" case. |

> **Seed-data gap for `RescheduleViewing`:** the two `confirmed` bookings belong to *different* clients
> (`booking-001` -> `u-client-1`, `booking-002` -> `u-client-2`), and `u-agent-1`'s only other booking
> (`booking-003`) is `cancelled`. No single caller therefore has two `confirmed` future bookings, so
> the tool's `booking_ambiguous` resolution path cannot be exercised against the current seed set.
> Backend Engineer: add one more `confirmed` future booking for `u-client-1` (against an existing
> `open` slot such as `avail-010`, and flip that slot to `booked`) so the ambiguity branch is testable
> without hand-crafting data per test run.

> **Seed-data note for escalation assignment:** all 15 seed properties carry an `agent_id`, so the
> "property with no listing agent" branch of `escalate_to_human`'s assignment rule needs a hand-built
> test fixture. Do **not** add an agent-less property to the seed set to cover it — that would weaken
> the `search_property` RBAC fixtures for no gain.
