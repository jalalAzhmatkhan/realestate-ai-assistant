"""The agent core: tool registration, the tool-calling loop, and RBAC re-authorization.

Everything here runs against ``FunctionModel``/``TestModel`` — no API key, no network, no
Docker. That is deliberate and not only a convenience: the properties worth asserting
(are all five tools registered under their required names, does a ``client`` caller's
LLM-supplied ``client_id`` get overridden, does an out-of-scope ``booking_id`` come back
as ``booking_not_found``) are properties of *this* code, and driving them through a real
model would make them non-deterministic for no added coverage.

The scripted ``FunctionModel`` stands in for the model's decision. It is a test double for
tool *selection*, not a routing layer: in production the LLM selects, and nothing under
``app/`` inspects a message to choose a tool. Whether the real model chooses *correctly*
(``RescheduleViewing`` vs ``BookViewing``, asking rather than auto-selecting on
``booking_ambiguous``) is prompt-level behavior and belongs to QA's task Q4.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlmodel import Session, select

from app.agent.deps import AgentDeps
from app.agent.orchestrator import build_agent, extract_tool_calls
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import (
    escalate_to_human,
    reschedule_viewing,
    schedule_viewing,
    search_property,
)
from app.booking.slots import cancel_booking
from app.db.session import build_engine, create_tables
from app.models import AvailabilitySlot, Booking, Conversation, Escalation, Property, User
from app.notifications.port import (
    BookingCreatedEvent,
    BookingRescheduledEvent,
    EscalationCreatedEvent,
)
from app.property.queries import scoped_property_query
from app.rag.index import FaqEntry, FaqIndex

from .conftest import make_db_settings, make_settings

NOW = datetime.now(timezone.utc)
T1 = NOW + timedelta(days=1)
T2 = NOW + timedelta(days=2)
T3 = NOW + timedelta(days=3)

ADMIN, AGENT, OTHER_AGENT = "u-admin-1", "u-agent-1", "u-agent-2"
CLIENT, OTHER_CLIENT = "u-client-1", "u-client-2"
ACTIVE, DRAFT, OTHER_ACTIVE = "prop-active", "prop-draft", "prop-other"

EXPECTED_TOOL_NAMES = {
    "search_faq",
    "SearchProperty",
    "BookViewing",
    "RescheduleViewing",
    "EscalateToHuman",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --------------------------------------------------------------------------- doubles


class RecordingNotifier:
    """Captures published events, so "emitted once, after commit" is assertable."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def publish_booking_created(self, event: BookingCreatedEvent) -> None:
        self.events.append(event)

    def publish_booking_rescheduled(self, event: BookingRescheduledEvent) -> None:
        self.events.append(event)

    def publish_booking_cancelled(self, event) -> None:
        self.events.append(event)

    def publish_escalation_created(self, event: EscalationCreatedEvent) -> None:
        self.events.append(event)


class StubEmbeddingModel:
    """Deterministic bag-of-words embeddings — no provider, no network, no torch.

    Substituting the embedding *model* rather than the index keeps ``app/rag/index.py``'s
    real cosine and threshold logic under test, which is the part carrying a contract
    ("below RAG_MIN_SCORE returns empty results, not an error").
    """

    VOCAB = ("deposit", "pet", "document", "rent", "cancel", "parking")

    @property
    def model_name(self) -> str:
        return "stub-embeddings"

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        return [1.0 if word in lowered else 0.0 for word in self.VOCAB]

    async def embed(self, inputs, *, input_type, settings=None):
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        vectors = [self._vector(text) for text in texts]

        class _Result:
            embeddings = vectors

        return _Result()


def scripted_model(*turns) -> FunctionModel:
    """Replays a fixed script, one entry per model request.

    Each entry is either a list of ``(tool_name, arguments)`` pairs or a final text reply.
    """
    state = {"index": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = turns[min(state["index"], len(turns) - 1)]
        state["index"] += 1
        if isinstance(turn, str):
            return ModelResponse(parts=[TextPart(turn)])
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in turn])

    return FunctionModel(respond)


def _as_payload(content) -> dict:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}
    return content.model_dump(mode="json")


class Run:
    """One agent run, exposing the three things worth asserting on.

    ``payloads`` is what each tool returned *to the model* — successes as the tool's own
    output, failures as the parsed ``{"error": {...}}`` envelope — so tests assert against
    what the model actually saw rather than a rendering of it.
    """

    def __init__(self, result) -> None:
        self.output = result.output
        self.records = extract_tool_calls(result.all_messages())
        self.payloads = [
            _as_payload(part.content)
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]

    def payload(self, index: int = 0) -> dict:
        return self.payloads[index]

    def error(self, index: int = 0) -> dict:
        return self.payloads[index]["error"]


async def run_tool(model_turns, deps) -> Run:
    agent = build_agent(scripted_model(*model_turns))
    return Run(await agent.run("(scripted)", deps=deps))


def _flatten(text: str) -> str:
    return " ".join(text.split())


def registered_tools() -> list:
    """The tool definitions the model is offered, captured from a real run."""
    captured: dict[str, list] = {}

    def capture(messages, info: AgentInfo) -> ModelResponse:
        captured["tools"] = info.function_tools
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(FunctionModel(capture))
    # No DB is touched: the run stops at the first model response, and only the dynamic
    # instructions read deps (for the caller's name/role).
    deps = AgentDeps(
        user=_user("u-schema-probe", "client"),
        db=None,  # type: ignore[arg-type]
        conversation_id="conv-schema-probe",
        rag=None,  # type: ignore[arg-type]
        settings=make_settings(),
        notifier=None,  # type: ignore[arg-type]
    )
    anyio.run(lambda: agent.run("hi", deps=deps))
    return captured["tools"]


# -------------------------------------------------------------------------- fixtures


def _user(user_id: str, role: str, status: str = "active") -> User:
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@evdekimi.test",
        role=role,
        hashed_password="x",
        status=status,
    )


def _property(property_id: str, *, status: str, agent_id: str | None, city: str = "Jakarta") -> Property:
    return Property(
        id=property_id,
        title=f"Title {property_id}",
        property_type="apartment",
        listing_type="rent",
        price=5_000_000,
        price_unit="per_month",
        bedrooms=2,
        bathrooms=1,
        area_sqm=60,
        address=f"Jl. {property_id}",
        city=city,
        latitude=-6.2,
        longitude=106.8,
        amenities=[],
        agent_id=agent_id,
        status=status,
        description=f"Description for {property_id}",
        listed_date=NOW.date(),
    )


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(make_db_settings(tmp_path, seed_on_startup=False))
    create_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        session.add_all(
            [
                _user(ADMIN, "admin"),
                _user(AGENT, "agent"),
                _user(OTHER_AGENT, "agent"),
                _user(CLIENT, "client"),
                _user(OTHER_CLIENT, "client"),
            ]
        )
        session.flush()
        session.add_all(
            [
                _property(ACTIVE, status="active", agent_id=AGENT),
                _property(DRAFT, status="draft", agent_id=AGENT),
                _property(OTHER_ACTIVE, status="active", agent_id=OTHER_AGENT, city="Bandung"),
            ]
        )
        session.flush()
        session.add_all(
            [
                AvailabilitySlot(id="slot-1", agent_id=AGENT, property_id=ACTIVE, slot_time=T1),
                AvailabilitySlot(id="slot-2", agent_id=AGENT, property_id=ACTIVE, slot_time=T2),
                AvailabilitySlot(id="slot-3", agent_id=AGENT, property_id=ACTIVE, slot_time=T3),
                AvailabilitySlot(
                    id="slot-other", agent_id=OTHER_AGENT, property_id=OTHER_ACTIVE, slot_time=T1
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Conversation(id="conv-test", user_id=CLIENT),
                Conversation(id="conv-agent", user_id=AGENT),
            ]
        )
        session.commit()
        yield session


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def faq_index():
    return FaqIndex(
        StubEmbeddingModel(),
        [
            FaqEntry(
                id="faq-deposit",
                question="How much is the deposit?",
                answer="The deposit is one month's rent.",
                category="fees",
                tags=("deposit",),
            ),
            FaqEntry(
                id="faq-pets",
                question="Can I keep a pet?",
                answer="Pets are allowed with written consent.",
                category="policy",
                tags=("pet",),
            ),
        ],
    )


def make_deps(db, notifier, faq_index, *, user_id, conversation_id="conv-test", **overrides):
    return AgentDeps(
        user=db.get(User, user_id),
        db=db,
        conversation_id=conversation_id,
        rag=faq_index,
        settings=make_settings(**overrides),
        notifier=notifier,
    )


# ---------------------------------------------------------------------- registration


def test_all_five_tools_are_registered_under_their_exposed_names():
    """The assessment mandates the literal names SearchProperty / BookViewing /
    EscalateToHuman; a grader inspecting the tool schema must find them verbatim."""
    assert {tool.name for tool in registered_tools()} == EXPECTED_TOOL_NAMES


def test_every_tool_carries_a_substantive_description():
    """Tool descriptions are the only lever on selection — a thin one is a correctness
    bug, not a cosmetic omission (README Design Decisions §3)."""
    for tool in registered_tools():
        assert tool.description and len(tool.description) > 200, tool.name


def test_the_two_prose_only_safety_rules_are_present_in_the_prompt_and_descriptions():
    """Never-auto-select and do-not-reveal exist *only* as prose (reschedule contract
    decision #8) — implementing them in code would violate CLAUDE.md's hard constraint,
    so nothing else in the system enforces them if this text is edited away."""
    # Whitespace-normalized so the assertions constrain the *wording*, not where the
    # prose happens to wrap.
    prompt = _flatten(SYSTEM_PROMPT)
    reschedule = _flatten(reschedule_viewing.TOOL_DESCRIPTION)

    assert "Do not pick one." in prompt
    assert "Say nothing about whether some particular booking exists." in prompt
    assert "ASK THE USER which one they mean" in reschedule
    assert "Do NOT pick a candidate yourself" in reschedule
    # Each booking tool points at the other, since choosing between them is exactly what
    # a keyword router would get wrong.
    assert "RescheduleViewing" in schedule_viewing.TOOL_DESCRIPTION
    assert "BookViewing" in reschedule


def test_search_property_schema_uses_property_type_never_bare_type():
    """X1 ruling: one vocabulary across the model, the tool schema, and the REST filter."""
    schema = next(t for t in registered_tools() if t.name == "SearchProperty").parameters_json_schema
    assert "property_type" in schema["properties"]
    assert "type" not in schema["properties"]


def test_reschedule_schema_carries_no_identity_fields():
    """Contract decision #4: omitting the field entirely is safer than accepting and
    overriding it — there is nothing for the model to hallucinate into."""
    schema = next(
        t for t in registered_tools() if t.name == "RescheduleViewing"
    ).parameters_json_schema
    assert {"client_id", "agent_id", "on_behalf_of"}.isdisjoint(schema["properties"])


# ------------------------------------------------------------------------ search_faq


@pytest.mark.anyio
async def test_search_faq_returns_grounded_hits(db, notifier, faq_index):
    run = await run_tool(
        [[("search_faq", {"query": "deposit"})], "answered"],
        make_deps(db, notifier, faq_index, user_id=CLIENT, rag_min_score=0.5),
    )
    assert [hit["id"] for hit in run.payload()["results"]] == ["faq-deposit"]


@pytest.mark.anyio
async def test_search_faq_below_the_score_floor_is_empty_not_an_error(db, notifier, faq_index):
    """Forcing a low-confidence policy answer is the failure this floor exists to
    prevent, so "nothing matched" must reach the model as a successful empty result."""
    run = await run_tool(
        [[("search_faq", {"query": "something entirely unrelated"})], "answered"],
        make_deps(db, notifier, faq_index, user_id=CLIENT, rag_min_score=0.5),
    )
    assert run.payload() == {"results": []}


@pytest.mark.anyio
async def test_search_faq_caps_the_models_requested_top_k(db, notifier, faq_index):
    """RAG_TOP_K is the server's ceiling; the LLM's top_k is only a hint."""
    run = await run_tool(
        [[("search_faq", {"query": "deposit pet", "top_k": 50})], "answered"],
        make_deps(db, notifier, faq_index, user_id=CLIENT, rag_top_k=1, rag_min_score=0.1),
    )
    assert len(run.payload()["results"]) == 1


# -------------------------------------------------------------------- SearchProperty


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user_id", "expected"),
    [
        (CLIENT, {ACTIVE, OTHER_ACTIVE}),
        (AGENT, {ACTIVE, DRAFT, OTHER_ACTIVE}),
        (OTHER_AGENT, {ACTIVE, OTHER_ACTIVE}),
        (ADMIN, {ACTIVE, DRAFT, OTHER_ACTIVE}),
    ],
)
async def test_visibility_follows_the_caller_not_the_arguments(
    db, notifier, faq_index, user_id, expected
):
    """Client: active only. Agent: all active plus their own in any status. Admin:
    unrestricted. Derived from ctx.deps.user — the schema has no identity argument."""
    run = await run_tool(
        [[("SearchProperty", {})], "done"],
        make_deps(db, notifier, faq_index, user_id=user_id),
    )
    assert {row["id"] for row in run.payload()["results"]} == expected


@pytest.mark.anyio
async def test_filters_cannot_widen_the_caller_scope(db, notifier, faq_index):
    """A filter narrows what the caller may already see; it is never a substitute for
    the scoping."""
    run = await run_tool(
        [[("SearchProperty", {"city": "Jakarta", "property_type": "apartment"})], "done"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert {row["id"] for row in run.payload()["results"]} == {ACTIVE}


@pytest.mark.anyio
async def test_no_matches_is_an_empty_list_not_an_error(db, notifier, faq_index):
    run = await run_tool(
        [[("SearchProperty", {"city": "Nowhere"})], "done"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.payload() == {"results": [], "count": 0}


@pytest.mark.anyio
async def test_geo_filter_ranks_by_distance_and_reports_it(db, notifier, faq_index):
    run = await run_tool(
        [
            [("SearchProperty", {"near_lat": -6.2, "near_lng": 106.8, "radius_km": 5})],
            "done",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    results = run.payload()["results"]
    assert results and all(row["distance_km"] is not None for row in results)
    assert results == sorted(results, key=lambda row: row["distance_km"])


# ---------------------------------------------------------------------- BookViewing


@pytest.mark.anyio
async def test_a_client_supplied_client_id_is_overridden(db, notifier, faq_index):
    """The single most consequential RBAC re-authorization in the system: a client can
    never book on someone else's behalf, whatever the model generates."""
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": OTHER_CLIENT,
                    },
                )
            ],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    booking = db.get(Booking, run.payload()["booking_id"])
    assert booking.client_id == CLIENT


@pytest.mark.anyio
async def test_booking_emits_exactly_one_created_event(db, notifier, faq_index):
    await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert len(notifier.events) == 1
    assert isinstance(notifier.events[0], BookingCreatedEvent)


@pytest.mark.anyio
async def test_an_agent_cannot_book_another_agents_listing(db, notifier, faq_index):
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": OTHER_ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": CLIENT,
                    },
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=AGENT, conversation_id="conv-agent"),
    )
    assert run.error()["code"] == "property_not_bookable"


@pytest.mark.anyio
async def test_staff_must_name_an_existing_client(db, notifier, faq_index):
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": "u-does-not-exist",
                    },
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=ADMIN),
    )
    assert run.error()["code"] == "client_not_found"


@pytest.mark.anyio
async def test_a_conflict_carries_suggested_alternatives(db, notifier, faq_index):
    """The same {availability_slot_id, slot_time} shape the REST endpoint returns, so all
    three booking surfaces describe a conflict identically."""
    await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    run = await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "conflict",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    error = run.error()
    assert error["code"] == "booking_slot_conflict"
    assert set(error["suggested_alternatives"][0]) == {"availability_slot_id", "slot_time"}


@pytest.mark.anyio
async def test_a_naive_datetime_is_rejected_rather_than_assumed_utc(db, notifier, faq_index):
    """Reading a naive datetime as UTC would move an Asia/Jakarta booking seven hours,
    which surfaces as the user turning up at the wrong time."""
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.replace(tzinfo=None).isoformat(),
                    },
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.error()["code"] == "slot_time_not_timezone_aware"


# ----------------------------------------------------------------- RescheduleViewing


async def _book(db, notifier, faq_index, *, user_id, slot_time, property_id=ACTIVE) -> str:
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {"property_id": property_id, "requested_slot_time": slot_time.isoformat()},
                )
            ],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=user_id),
    )
    return run.payload()["booking_id"]


@pytest.mark.anyio
async def test_the_only_upcoming_booking_resolves_without_an_id(db, notifier, faq_index):
    """The common case: a chat user says "move my viewing" and knows no booking id."""
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    notifier.events.clear()

    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T2.isoformat()})], "moved"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    payload = run.payload()
    assert payload["booking_id"] == booking_id
    assert payload["rescheduled_count"] == 1
    assert len(notifier.events) == 1
    assert isinstance(notifier.events[0], BookingRescheduledEvent)


@pytest.mark.anyio
async def test_another_users_booking_is_indistinguishable_from_a_missing_one(
    db, notifier, faq_index
):
    """The resolver *is* the authorization boundary. A distinguishable "forbidden" would
    turn the agent into an oracle a user could drive to enumerate booking ids."""
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    out_of_scope = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T2.isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    nonexistent = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": "booking-nope", "requested_slot_time": T2.isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_CLIENT),
    )
    assert out_of_scope.error()["code"] == "booking_not_found"
    # Byte-identical, so nothing downstream — including the model — can tell them apart.
    assert out_of_scope.error() == nonexistent.error()


@pytest.mark.anyio
async def test_ambiguity_returns_field_limited_candidates(db, notifier, faq_index):
    """Candidates carry booking_id/property_title/slot_time only: a client
    disambiguating their own viewings must not receive anyone's personal data
    (contract decision #9)."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T2)

    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T3.isoformat()})], "which one?"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()
    assert error["code"] == "booking_ambiguous"
    assert len(error["candidates"]) == 2
    for candidate in error["candidates"]:
        assert set(candidate) == {"booking_id", "property_title", "slot_time"}
    # The do-not-auto-select instruction travels with the error itself, rather than
    # living in code that inspects what the model does next.
    assert "Never choose one yourself" in error["guidance"]


@pytest.mark.anyio
async def test_hints_narrow_an_otherwise_ambiguous_match(db, notifier, faq_index):
    first = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T2)

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {
                        "requested_slot_time": T3.isoformat(),
                        "current_slot_time": T1.isoformat(),
                    },
                )
            ],
            "moved",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.payload()["booking_id"] == first


@pytest.mark.anyio
async def test_rescheduling_to_the_same_time_writes_nothing(db, notifier, faq_index):
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    notifier.events.clear()

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T1.isoformat()},
                )
            ],
            "already then",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.error()["code"] == "slot_unchanged"
    assert notifier.events == []
    assert db.get(Booking, booking_id).rescheduled_count == 0


@pytest.mark.anyio
async def test_admin_can_reschedule_any_booking_by_id(db, notifier, faq_index):
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T2.isoformat()},
                )
            ],
            "moved",
        ],
        make_deps(db, notifier, faq_index, user_id=ADMIN),
    )
    assert run.payload()["booking_id"] == booking_id


# ------------------------------------------------------------------- EscalateToHuman


@pytest.mark.anyio
async def test_escalation_is_tied_to_the_caller_and_left_unassigned(db, notifier, faq_index):
    """B18a is the unassigned path only — listing-agent assignment is B18b (Phase 4)."""
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {
                        "reason": "wants a human",
                        "conversation_summary": "asked about a refund",
                        "category": "complaint",
                    },
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    payload = run.payload()
    assert payload["status"] == "queued_unassigned"
    assert payload["assigned_agent_id"] is None

    escalation = db.get(Escalation, payload["escalation_id"])
    assert escalation.escalated_by_id == CLIENT
    assert escalation.conversation_id == "conv-test"
    assert len(notifier.events) == 1
    assert isinstance(notifier.events[0], EscalationCreatedEvent)


@pytest.mark.anyio
async def test_an_out_of_scope_property_id_is_stored_as_null(db, notifier, faq_index):
    """An LLM-supplied id for a listing the caller cannot see resolves to null, so
    "escalations for property X" stays correct and no existence oracle is opened."""
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {
                        "reason": "question about a listing",
                        "conversation_summary": "asked about the draft unit",
                        "property_id": DRAFT,
                    },
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    escalation = db.get(Escalation, run.payload()["escalation_id"])
    assert escalation.property_id is None


@pytest.mark.anyio
async def test_the_rate_limit_stops_rows_not_answers(db, notifier, faq_index):
    """The safety valve must stay available: past the limit we stop writing rows, not
    stop giving the user a reference."""
    deps = make_deps(db, notifier, faq_index, user_id=CLIENT)
    run = None
    for _ in range(4):
        run = await run_tool(
            [
                [("EscalateToHuman", {"reason": "again", "conversation_summary": "again"})],
                "logged",
            ],
            deps,
        )
    assert run.payload()["escalation_id"] is not None
    persisted = db.exec(
        select(Escalation).where(Escalation.conversation_id == "conv-test")
    ).all()
    assert len(persisted) == 3


# ------------------------------------------------------------------------- run loop


@pytest.mark.anyio
async def test_the_model_can_chain_tools_across_turns(db, notifier, faq_index):
    """Search, then book from the result, in one run — the multi-step case a hardcoded
    router cannot express."""
    run = await run_tool(
        [
            [("SearchProperty", {"city": "Jakarta"})],
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "Booked your viewing.",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert [record.tool for record in run.records] == ["SearchProperty", "BookViewing"]
    assert run.output == "Booked your viewing."


@pytest.mark.anyio
async def test_parallel_tool_calls_in_one_turn_are_all_recorded(db, notifier, faq_index):
    run = await run_tool(
        [
            [("search_faq", {"query": "deposit"}), ("SearchProperty", {"city": "Jakarta"})],
            "done",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT, rag_min_score=0.5),
    )
    assert [record.tool for record in run.records] == ["search_faq", "SearchProperty"]


@pytest.mark.anyio
async def test_tool_call_records_carry_the_arguments_the_model_generated(
    db, notifier, faq_index
):
    """tool_calls[] is what makes autonomous selection observable rather than asserted."""
    run = await run_tool(
        [[("SearchProperty", {"city": "Jakarta", "bedrooms": 2})], "done"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.records[0].arguments == {"city": "Jakarta", "bedrooms": 2}
    assert run.records[0].result_summary


@pytest.mark.anyio
async def test_a_failing_tool_returns_to_the_model_rather_than_ending_the_run(
    db, notifier, faq_index
):
    """A tool failure must be recoverable: the model sees the structured error and
    replies, instead of the run raising into the endpoint."""
    run = await run_tool(
        [
            [("RescheduleViewing", {"booking_id": "nope", "requested_slot_time": T2.isoformat()})],
            "I could not find a viewing to move.",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    assert run.output == "I could not find a viewing to move."
    assert run.records[0].tool == "RescheduleViewing"


# ============================================================================
# QA (Q2/Q4) extensions — contract clauses the implementation pass left untested.
# Grouped at the end rather than interleaved so the implementation author's
# self-verification set stays legible as its own body of work.
# ============================================================================


# ------------------------------------------------- one conflict implementation, shared


def test_neither_booking_tool_reaches_for_availability_itself():
    """README Design Decisions §9: availability resolution and conflict detection live in
    ``app/booking/slots.py`` and nowhere else. A tool module that imported
    ``AvailabilitySlot`` would be one refactor away from a second, divergent conflict
    check — and the chat surface disagreeing with the dashboard about availability is a
    data-integrity bug, not a cosmetic one."""
    for module in (schedule_viewing, reschedule_viewing):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "AvailabilitySlot" not in source, module.__name__


@pytest.mark.anyio
async def test_book_viewing_delegates_the_claim_to_slots(db, notifier, faq_index, monkeypatch):
    """The tool's own job is RBAC re-authorization and error rendering; the slot claim is
    delegated with the *re-authorized* client id, not the one the model supplied."""
    calls = []
    original = schedule_viewing.create_booking

    def spy(session, **kwargs):
        calls.append(kwargs)
        return original(session, **kwargs)

    monkeypatch.setattr(schedule_viewing, "create_booking", spy)

    await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": OTHER_CLIENT,
                    },
                )
            ],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert len(calls) == 1
    assert calls[0]["client_id"] == CLIENT
    assert calls[0]["property_id"] == ACTIVE


# ------------------------------------------------------------ BookViewing, staff paths


@pytest.mark.anyio
@pytest.mark.parametrize("user_id", [ADMIN, AGENT])
async def test_staff_omitting_client_id_are_asked_who_it_is_for(
    db, notifier, faq_index, user_id
):
    """Documented deviation #4 of the Phase 3 checkpoint, asserted rather than assumed.
    Defaulting to the staff member's own id would create a booking whose client is the
    agent conducting the viewing — a wrong row that looks right."""
    run = await run_tool(
        [
            [("BookViewing", {"property_id": ACTIVE, "requested_slot_time": T1.isoformat()})],
            "who for?",
        ],
        make_deps(
            db,
            notifier,
            faq_index,
            user_id=user_id,
            conversation_id="conv-agent" if user_id == AGENT else "conv-test",
        ),
    )
    error = run.error()

    assert error["code"] == "client_id_required"
    assert "Ask the user which client this viewing is for." in error["guidance"]
    assert db.exec(select(Booking)).all() == []


@pytest.mark.anyio
async def test_an_admin_may_book_for_a_named_client(db, notifier, faq_index):
    """The other half of the override rule: staff *may* act for a client, so the override
    must not have become a blanket "always use the caller"."""
    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": OTHER_CLIENT,
                    },
                )
            ],
            "booked",
        ],
        make_deps(db, notifier, faq_index, user_id=ADMIN),
    )

    assert run.payload()["client_id"] == OTHER_CLIENT


@pytest.mark.anyio
async def test_a_disabled_client_cannot_be_booked_for(db, notifier, faq_index):
    disabled = _user("u-client-disabled", "client", status="disabled")
    db.add(disabled)
    db.commit()

    run = await run_tool(
        [
            [
                (
                    "BookViewing",
                    {
                        "property_id": ACTIVE,
                        "requested_slot_time": T1.isoformat(),
                        "client_id": disabled.id,
                    },
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=ADMIN),
    )

    assert run.error()["code"] == "client_not_found"


# -------------------------------------------------- RescheduleViewing, resolution paths


@pytest.mark.anyio
async def test_reschedule_rejects_a_naive_datetime_too(db, notifier, faq_index):
    """``BookViewing`` has this test; the reschedule path is a separate check in a
    separate module and would regress independently."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"requested_slot_time": T2.replace(tzinfo=None).isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.error()["code"] == "slot_time_not_timezone_aware"
    # Rejected before resolution, so a naive time cannot also leak a candidate list on
    # the way out.
    assert "candidates" not in run.error()


@pytest.mark.anyio
async def test_property_id_alone_resolves_an_otherwise_ambiguous_match(
    db, notifier, faq_index
):
    """The contract's resolution table lists ``property_id``-only as its own row;
    ``current_slot_time``-only is already covered."""
    await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    other = await _book(
        db, notifier, faq_index, user_id=CLIENT, slot_time=T1, property_id=OTHER_ACTIVE
    )

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"requested_slot_time": T2.isoformat(), "property_id": ACTIVE},
                )
            ],
            "moved",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.payload()["property_id"] == ACTIVE
    assert run.payload()["booking_id"] != other


@pytest.mark.anyio
async def test_candidates_are_capped_at_the_five_soonest(db, notifier, faq_index):
    """Contract decision #9. Uncapped, an admin's ambiguity payload would be the whole
    booking table — both a context-window problem and a disclosure one."""
    times = [NOW + timedelta(days=10 + index) for index in range(7)]
    db.add_all(
        [
            AvailabilitySlot(
                id=f"slot-many-{index}",
                agent_id=AGENT,
                property_id=ACTIVE,
                slot_time=slot_time,
            )
            for index, slot_time in enumerate(times)
        ]
    )
    db.commit()
    for slot_time in times:
        await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=slot_time)

    run = await run_tool(
        [[("RescheduleViewing", {"requested_slot_time": T1.isoformat()})], "which?"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    candidates = run.error()["candidates"]

    assert len(candidates) == 5
    returned = [candidate["slot_time"] for candidate in candidates]
    assert returned == sorted(returned)
    # The five *soonest*, not the first five the database happened to return.
    assert returned[0] == times[0].astimezone(timezone.utc).isoformat()


@pytest.mark.anyio
async def test_an_agent_resolves_only_bookings_on_their_own_listings(
    db, notifier, faq_index
):
    """The agent row of the reschedule RBAC table. ``agent_id`` scoping is a different
    column from ``client_id`` scoping and would regress on its own."""
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    owner = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T2.isoformat()},
                )
            ],
            "moved",
        ],
        make_deps(db, notifier, faq_index, user_id=AGENT, conversation_id="conv-agent"),
    )
    assert owner.payload()["booking_id"] == booking_id

    intruder = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T3.isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=OTHER_AGENT, conversation_id="conv-agent"),
    )
    assert intruder.error()["code"] == "booking_not_found"


@pytest.mark.anyio
async def test_a_cancelled_booking_cannot_be_moved(db, notifier, faq_index):
    """Reachable only by explicit id — the hint-filtered path already excludes
    non-``confirmed`` rows, so this is the branch a model supplying an id from earlier in
    the conversation hits."""
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    cancel_booking(db, db.get(Booking, booking_id))

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T2.isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    error = run.error()

    assert error["code"] == "booking_not_reschedulable"
    assert "offer to book a new one" in error["guidance"]


@pytest.mark.anyio
async def test_a_past_booking_is_treated_as_completed(db, notifier, faq_index):
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)
    booking = db.get(Booking, booking_id)
    booking.slot_time = NOW - timedelta(days=1)
    db.add(booking)
    db.commit()

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {"booking_id": booking_id, "requested_slot_time": T2.isoformat()},
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.error()["code"] == "booking_not_reschedulable"


@pytest.mark.anyio
async def test_a_past_requested_time_is_rejected_without_touching_the_booking(
    db, notifier, faq_index
):
    booking_id = await _book(db, notifier, faq_index, user_id=CLIENT, slot_time=T1)

    run = await run_tool(
        [
            [
                (
                    "RescheduleViewing",
                    {
                        "booking_id": booking_id,
                        "requested_slot_time": (NOW - timedelta(days=1)).isoformat(),
                    },
                )
            ],
            "sorry",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.error()["code"] == "slot_time_in_past"
    assert db.get(Booking, booking_id).rescheduled_count == 0


# --------------------------------------------------------- SearchProperty, scope + shape


def test_the_search_schema_carries_no_identity_or_visibility_argument():
    """README Design Decisions §5: visibility comes from ``ctx.deps.user``, so there must
    be nothing in the schema for the model to set. A ``status`` argument would let a
    client *ask* for drafts — harmless today because the scoped query still filters them,
    and exactly the kind of belt-only arrangement that breaks when the query is edited."""
    schema = next(
        tool for tool in registered_tools() if tool.name == "SearchProperty"
    ).parameters_json_schema

    assert {"client_id", "agent_id", "user_id", "owner_id", "status"}.isdisjoint(
        schema["properties"]
    )


@pytest.mark.parametrize(
    ("user_id", "role", "expected"),
    [
        (CLIENT, "client", {ACTIVE, OTHER_ACTIVE}),
        (AGENT, "agent", {ACTIVE, DRAFT, OTHER_ACTIVE}),
        (ADMIN, "admin", {ACTIVE, DRAFT, OTHER_ACTIVE}),
    ],
)
def test_the_shared_scoped_query_is_the_one_the_rest_endpoint_will_use(
    db, user_id, role, expected
):
    """Asserted against ``app/property/queries.py`` directly, not only through the tool.

    The tool and ``GET /api/v1/properties`` are supposed to consume one builder; testing
    only through the tool would let the builder be correct for chat and the endpoint wrap
    it in something looser without any test noticing."""
    user = db.get(User, user_id)
    assert user.role == role

    visible = db.exec(scoped_property_query(user)).all()

    assert {prop.id for prop in visible} == expected


def test_an_agent_sees_their_own_drafts_but_not_another_agents(db):
    db.add(_property("prop-other-draft", status="draft", agent_id=OTHER_AGENT))
    db.commit()

    ids = {prop.id for prop in db.exec(scoped_property_query(db.get(User, AGENT))).all()}

    assert DRAFT in ids
    assert "prop-other-draft" not in ids


@pytest.mark.anyio
async def test_the_result_limit_is_capped_server_side(db, notifier, faq_index):
    """``limit`` is a model-supplied hint. Uncapped, one hallucinated ``limit=100000``
    would pull the whole table into the context window."""
    db.add_all(
        [
            _property(f"prop-bulk-{index}", status="active", agent_id=AGENT)
            for index in range(30)
        ]
    )
    db.commit()

    run = await run_tool(
        [[("SearchProperty", {"limit": 10_000})], "done"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.payload()["count"] == search_property.MAX_LIMIT


@pytest.mark.anyio
async def test_a_zero_limit_still_returns_something(db, notifier, faq_index):
    """``limit=0`` from the model must not read as "return nothing", which the model
    would relay to the user as "no listings matched"."""
    run = await run_tool(
        [[("SearchProperty", {"limit": 0})], "done"],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert run.payload()["count"] == 1


# ---------------------------------------------------- EscalateToHuman, the safety valve


@pytest.mark.anyio
@pytest.mark.parametrize("user_id", [ADMIN, AGENT, CLIENT])
async def test_every_role_may_escalate(db, notifier, faq_index, user_id):
    """The one tool with no role restriction at all. A role check creeping in here would
    remove the fallback every other tool's failure path points at."""
    run = await run_tool(
        [
            [("EscalateToHuman", {"reason": "needs a person", "conversation_summary": "s"})],
            "logged",
        ],
        make_deps(
            db,
            notifier,
            faq_index,
            user_id=user_id,
            conversation_id="conv-agent" if user_id == AGENT else "conv-test",
        ),
    )

    assert run.payload()["escalation_id"] is not None
    assert db.get(Escalation, run.payload()["escalation_id"]).escalated_by_id == user_id


@pytest.mark.anyio
async def test_the_rate_limit_is_derived_from_rows_so_it_survives_another_instance(
    engine, db, notifier, faq_index
):
    """Checkpoint decision #7: counting persisted rows rather than an in-process counter.

    Simulated by running the fourth escalation through a **different Session on the same
    database** — the closest in-process stand-in for a second application instance. A
    per-process dict would let the limit reset here, which is exactly the bug a
    single-session test can never see.
    """
    for _ in range(3):
        await run_tool(
            [
                [("EscalateToHuman", {"reason": "again", "conversation_summary": "again"})],
                "logged",
            ],
            make_deps(db, notifier, faq_index, user_id=CLIENT),
        )

    with Session(engine) as other_instance:
        run = await run_tool(
            [
                [("EscalateToHuman", {"reason": "again", "conversation_summary": "again"})],
                "logged",
            ],
            AgentDeps(
                user=other_instance.get(User, CLIENT),
                db=other_instance,
                conversation_id="conv-test",
                rag=faq_index,
                settings=make_settings(),
                notifier=notifier,
            ),
        )
        persisted = other_instance.exec(
            select(Escalation).where(Escalation.conversation_id == "conv-test")
        ).all()

    assert len(persisted) == 3
    # Still answered, with the existing reference — the valve throttles rows, not replies.
    assert run.payload()["escalation_id"] is not None


@pytest.mark.anyio
async def test_the_rate_limit_is_scoped_to_one_conversation(db, notifier, faq_index):
    """A user who exhausted one conversation must still be able to escalate a genuinely
    new one; a global per-user limit would lock them out of support entirely."""
    for _ in range(3):
        await run_tool(
            [
                [("EscalateToHuman", {"reason": "again", "conversation_summary": "again"})],
                "logged",
            ],
            make_deps(db, notifier, faq_index, user_id=CLIENT),
        )
    db.add(Conversation(id="conv-second", user_id=CLIENT))
    db.commit()

    run = await run_tool(
        [
            [("EscalateToHuman", {"reason": "new topic", "conversation_summary": "new"})],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT, conversation_id="conv-second"),
    )

    assert db.get(Escalation, run.payload()["escalation_id"]).conversation_id == "conv-second"


@pytest.mark.anyio
async def test_an_out_of_scope_property_id_resolves_exactly_like_a_nonexistent_one(
    db, notifier, faq_index
):
    """Checkpoint decision #2 / assignment contract decision 6: the two must be
    indistinguishable, or a client could probe for a draft listing's existence by watching
    whether the escalation came back assigned."""

    async def escalate(property_id):
        return await run_tool(
            [
                [
                    (
                        "EscalateToHuman",
                        {
                            "reason": "about a listing",
                            "conversation_summary": "s",
                            "property_id": property_id,
                        },
                    )
                ],
                "logged",
            ],
            make_deps(db, notifier, faq_index, user_id=CLIENT),
        )

    hidden = (await escalate(DRAFT)).payload()
    missing = (await escalate("prop-never-existed")).payload()

    assert db.get(Escalation, hidden["escalation_id"]).property_id is None
    assert db.get(Escalation, missing["escalation_id"]).property_id is None

    def without_the_reference(payload):
        # The reference id necessarily differs between two escalations; everything the
        # model could reason about must not.
        return {
            key: value.replace(payload["escalation_id"], "<ref>")
            if isinstance(value, str)
            else value
            for key, value in payload.items()
            if key != "escalation_id"
        }

    assert without_the_reference(hidden) == without_the_reference(missing)


@pytest.mark.anyio
async def test_a_visible_property_id_is_persisted_on_the_escalation(db, notifier, faq_index):
    """The positive control for the test above: resolution genuinely works, so "always
    null" is not what makes the two cases indistinguishable."""
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {
                        "reason": "about a listing",
                        "conversation_summary": "s",
                        "property_id": ACTIVE,
                    },
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )

    assert db.get(Escalation, run.payload()["escalation_id"]).property_id == ACTIVE


@pytest.mark.anyio
async def test_the_escalation_event_carries_no_free_text(db, notifier, faq_index):
    """README Design Decisions §8: ``reason``/``conversation_summary`` are free text the
    user may have typed personal detail into, and a fan-out event is the wrong place for
    it. A consumer that needs them fetches by ``escalation_id``."""
    run = await run_tool(
        [
            [
                (
                    "EscalateToHuman",
                    {
                        "reason": "my phone number is 0812-secret",
                        "conversation_summary": "user shared contact details",
                    },
                )
            ],
            "logged",
        ],
        make_deps(db, notifier, faq_index, user_id=CLIENT),
    )
    event = notifier.events[-1]

    assert isinstance(event, EscalationCreatedEvent)
    assert not hasattr(event, "reason")
    assert not hasattr(event, "conversation_summary")
    assert event.escalation_id == run.payload()["escalation_id"]


# ------------------------------------- B18b: listing-agent assignment, the E1-E8 table
#
# Documentation/audits/2026-08-05-escalation-assignment-contract.md § Decision 3. The
# whole table is here rather than split by outcome because E2 and E3 are only meaningful
# next to E8: "always unassigned" would satisfy six of the eight rows on its own.

NULL_OWNER_PROPERTY = "prop-unowned"
DISABLED_OWNER_PROPERTY = "prop-disabled-owner"
DOWNGRADED_OWNER_PROPERTY = "prop-downgraded-owner"
DISABLED_AGENT = "u-agent-disabled"
DOWNGRADED_AGENT = "u-agent-downgraded"


@pytest.fixture
def assignment_fixtures(db):
    """The three listings no seed row can express: an unowned one (E4), one owned by a
    disabled account (E5), and one owned by an account whose role was downgraded off
    ``agent``/``admin`` (E6)."""
    db.add_all(
        [
            _user(DISABLED_AGENT, "agent", status="disabled"),
            _user(DOWNGRADED_AGENT, "client"),
        ]
    )
    db.flush()
    db.add_all(
        [
            _property(NULL_OWNER_PROPERTY, status="active", agent_id=None),
            _property(DISABLED_OWNER_PROPERTY, status="active", agent_id=DISABLED_AGENT),
            _property(DOWNGRADED_OWNER_PROPERTY, status="active", agent_id=DOWNGRADED_AGENT),
        ]
    )
    db.commit()
    return db


async def _escalate(db, notifier, faq_index, *, user_id, property_id=None, conversation_id="conv-test"):
    arguments = {"reason": "about a listing", "conversation_summary": "s"}
    if property_id is not None:
        arguments["property_id"] = property_id
    return await run_tool(
        [[("EscalateToHuman", arguments)], "logged"],
        make_deps(db, notifier, faq_index, user_id=user_id, conversation_id=conversation_id),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "user_id", "property_id"),
    [
        ("E1 no property_id supplied", CLIENT, None),
        ("E2 property_id does not exist", CLIENT, "prop-never-existed"),
        ("E3 property_id outside the caller's scope", CLIENT, DRAFT),
        ("E4 property has no agent", CLIENT, NULL_OWNER_PROPERTY),
        ("E5 listing agent is disabled", CLIENT, DISABLED_OWNER_PROPERTY),
        ("E6 listing agent is no longer an agent", CLIENT, DOWNGRADED_OWNER_PROPERTY),
    ],
)
async def test_every_negative_assignment_branch_degrades_to_unassigned(
    assignment_fixtures, notifier, faq_index, case, user_id, property_id
):
    """E1-E6: each returns a *successful* result with no assignee. None of them may
    fail the call — this tool is what every other tool's failure path points at."""
    run = await _escalate(
        assignment_fixtures, notifier, faq_index, user_id=user_id, property_id=property_id
    )
    payload = run.payload()

    assert payload["status"] == "queued_unassigned", case
    assert payload["assigned_agent_id"] is None, case
    assert payload["escalation_id"] is not None, case


@pytest.mark.anyio
async def test_e7_an_agent_escalating_about_their_own_listing_is_assigned_to_themselves(
    db, notifier, faq_index
):
    """Decision 4: self-assignment is deliberate. The record means "this concerns
    listing L, whose owner is agent A", which holds whoever typed the message."""
    run = await _escalate(
        db, notifier, faq_index, user_id=AGENT, property_id=ACTIVE, conversation_id="conv-agent"
    )
    payload = run.payload()

    assert payload["status"] == "queued"
    assert payload["assigned_agent_id"] == AGENT
    escalation = db.get(Escalation, payload["escalation_id"])
    assert escalation.escalated_by_id == escalation.assigned_agent_id == AGENT


@pytest.mark.anyio
async def test_e8_a_visible_listing_with_an_active_agent_is_assigned(db, notifier, faq_index):
    run = await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=ACTIVE)
    payload = run.payload()

    assert payload["status"] == "queued"
    assert payload["assigned_agent_id"] == AGENT
    escalation = db.get(Escalation, payload["escalation_id"])
    assert escalation.assigned_agent_id == AGENT
    assert escalation.property_id == ACTIVE
    assert escalation.status == "queued"


@pytest.mark.anyio
async def test_e5_and_e6_are_only_unassigned_because_the_account_cannot_act(
    assignment_fixtures, notifier, faq_index
):
    """Positive control for E5/E6: re-enabling the account and restoring the role turns
    the same call into an assignment, so "always unassigned" is not what produced them."""
    db = assignment_fixtures
    revived = db.get(User, DISABLED_AGENT)
    revived.status = "active"
    db.add(revived)
    db.commit()

    run = await _escalate(
        db, notifier, faq_index, user_id=CLIENT, property_id=DISABLED_OWNER_PROPERTY
    )

    assert run.payload()["assigned_agent_id"] == DISABLED_AGENT


@pytest.mark.anyio
@pytest.mark.parametrize("hidden_property", [DRAFT, "prop-other-sold"])
async def test_a_hidden_listing_and_a_nonexistent_one_produce_byte_identical_output(
    db, notifier, faq_index, hidden_property
):
    """The enumeration oracle Decision 2 exists to close, asserted on the whole
    serialized tool output rather than on the status field.

    Both hidden listings have an *assignable* owner, so a raw primary-key read would
    return ``queued`` here and hand a ``client`` proof that the listing exists and who
    owns it.
    """
    db.add(_property("prop-other-sold", status="sold", agent_id=OTHER_AGENT))
    db.commit()

    hidden = (await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=hidden_property)).payload()
    missing = (await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id="prop-never-existed")).payload()

    def normalized(payload: dict) -> str:
        # Only the reference id may differ between two distinct escalations.
        return json.dumps(payload, sort_keys=True).replace(payload["escalation_id"], "<ref>")

    assert normalized(hidden) == normalized(missing)


@pytest.mark.anyio
async def test_the_owning_agent_sees_the_assignment_the_client_cannot(db, notifier, faq_index):
    """The asymmetry Decision 2 predicts, stated as a test: the same ``property_id``
    resolves for the owner and not for a ``client``."""
    as_client = (await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=DRAFT)).payload()
    as_owner = (
        await _escalate(
            db, notifier, faq_index, user_id=AGENT, property_id=DRAFT, conversation_id="conv-agent"
        )
    ).payload()

    assert as_client["assigned_agent_id"] is None
    assert as_owner["assigned_agent_id"] == AGENT


@pytest.mark.anyio
async def test_a_database_failure_during_assignment_still_logs_the_escalation(
    db, notifier, faq_index, monkeypatch
):
    """The one hard invariant: assignment resolution must never become a new way for the
    safety valve to fail. A raising lookup degrades to unassigned and proceeds."""

    def explode(*args, **kwargs):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(escalate_to_human, "find_visible_property", explode)

    run = await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=ACTIVE)
    payload = run.payload()

    assert payload["escalation_id"] is not None
    assert payload["status"] == "queued_unassigned"
    assert payload["assigned_agent_id"] is None
    assert db.get(Escalation, payload["escalation_id"]) is not None


@pytest.mark.anyio
async def test_the_escalation_event_carries_the_assignee_and_status(db, notifier, faq_index):
    """Decision 5: a queue consumer's first question is "who is this for", and it must
    not have to re-query the escalation table to answer it."""
    run = await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=ACTIVE)
    event = notifier.events[-1]

    assert isinstance(event, EscalationCreatedEvent)
    assert event.assigned_agent_id == AGENT
    assert event.status == "queued"
    assert event.property_id == ACTIVE
    assert event.escalated_by_id == CLIENT
    assert event.escalated_by_role == "client"
    assert event.escalation_id == run.payload()["escalation_id"]
    assert not hasattr(event, "reason")
    assert not hasattr(event, "conversation_summary")


@pytest.mark.anyio
async def test_an_unresolvable_property_id_is_absent_from_the_event_too(
    db, notifier, faq_index
):
    """Decision 6 applies to the event, not only the row — a dangling id in a fan-out
    payload misleads every consumer downstream of it."""
    await _escalate(db, notifier, faq_index, user_id=CLIENT, property_id=DRAFT)

    assert notifier.events[-1].property_id is None
