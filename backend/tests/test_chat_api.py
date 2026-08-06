"""``POST /api/v1/chat/messages`` end to end, against a scripted model.

The endpoint's whole job is: resolve identity, run the agent, persist, respond. These
tests assert exactly that — that the *authenticated* user (not anything the model
produced) reaches the tools, that the turn is persisted with the tools the model chose,
and that another user's conversation is unreachable. Which tool the model picks is out of
scope here by design; it is the model's decision, and this file's fixture makes it.

Runs on the seeded database and a ``FunctionModel``-backed agent injected through
``create_app(..., agent=...)``, so nothing here needs an API key or a network.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from sqlmodel import Session, select

from app.agent.orchestrator import build_agent
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import Booking, Conversation, Message
from app.rag.index import FaqEntry

from .conftest import SEED_PASSWORD, make_db_settings
from .rag_doubles import InMemoryFaqIndex
from .test_agent import RecordingNotifier, StubEmbeddingModel

CHAT = "/api/v1/chat/messages"
LOGIN = "/api/v1/auth/login"
CLIENT_EMAIL = "andi.client@evdekimi.test"
OTHER_CLIENT_EMAIL = "maria.client@evdekimi.test"


def scripted(*turns):
    state = {"index": 0}

    def respond(messages, info) -> ModelResponse:
        turn = turns[min(state["index"], len(turns) - 1)]
        state["index"] += 1
        if isinstance(turn, str):
            return ModelResponse(parts=[TextPart(turn)])
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in turn])

    return FunctionModel(respond)


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def faq_index():
    return InMemoryFaqIndex(
        StubEmbeddingModel(),
        [
            FaqEntry(
                id="faq-deposit",
                question="How much is the deposit?",
                answer="The deposit is one month's rent.",
                category="fees",
                tags=("deposit",),
            )
        ],
    )


@pytest.fixture
def make_client(settings, faq_index):
    def _make(model):
        app = create_app(
            settings,
            agent=build_agent(model),
            faq_index=faq_index,
            notifier=RecordingNotifier(),
        )
        return TestClient(app)

    return _make


def bearer(client, email=CLIENT_EMAIL) -> dict:
    response = client.post(LOGIN, json={"email": email, "password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_unauthenticated_callers_are_rejected_before_the_agent_runs(make_client):
    with make_client(scripted("hello")) as client:
        response = client.post(CHAT, json={"message": "hi"})
    assert response.status_code == 401


def test_a_new_conversation_is_created_and_returned(make_client):
    with make_client(scripted("Hello, how can I help?")) as client:
        response = client.post(CHAT, json={"message": "hi"}, headers=bearer(client))

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["conversation_id"].startswith("conv-")
    assert body["reply"] == "Hello, how can I help?"
    assert body["tool_calls"] == []


def test_the_tools_the_model_chose_are_returned_and_persisted(make_client):
    """`tool_calls[]` is what makes autonomous selection observable — a response carrying
    only `reply` would be indistinguishable from a hardcoded flow."""
    model = scripted(
        [("SearchProperty", {"city": "Jakarta Selatan"})],
        "I found some listings.",
    )
    with make_client(model) as client:
        response = client.post(
            CHAT, json={"message": "find me something"}, headers=bearer(client)
        )
        body = response.json()
        engine = client.app.state.engine

    assert [call["tool"] for call in body["tool_calls"]] == ["SearchProperty"]
    assert body["tool_calls"][0]["arguments"] == {"city": "Jakarta Selatan"}

    with Session(engine) as session:
        messages = session.exec(
            select(Message).where(Message.conversation_id == body["conversation_id"])
        ).all()
    roles = {message.role: message for message in messages}
    assert roles["user"].content == "find me something"
    assert roles["assistant"].content == "I found some listings."
    # The gap Phase 1's QA flagged: the assistant's tool calls are now on the row, not
    # only in the live response.
    assert roles["assistant"].tool_calls[0]["tool"] == "SearchProperty"
    assert roles["user"].tool_calls is None


def test_a_second_turn_continues_the_same_conversation_with_history(make_client):
    with make_client(scripted("first", "second")) as client:
        headers = bearer(client)
        first = client.post(CHAT, json={"message": "hi"}, headers=headers).json()
        second = client.post(
            CHAT,
            json={"message": "and again", "conversation_id": first["conversation_id"]},
            headers=headers,
        ).json()
        engine = client.app.state.engine

    assert second["conversation_id"] == first["conversation_id"]
    with Session(engine) as session:
        conversation = session.get(Conversation, first["conversation_id"])
        messages = session.exec(
            select(Message).where(Message.conversation_id == conversation.id)
        ).all()
    assert len(messages) == 4
    # Serialized agent history, so the next turn sees the tool results of this one —
    # a booking_ambiguous candidate list, for instance, survives into the reply.
    assert conversation.history_json


def test_another_users_conversation_is_not_found_never_forbidden(make_client):
    """Same posture as bookings: a 403 here would confirm the conversation exists."""
    with make_client(scripted("ok")) as client:
        owner = client.post(CHAT, json={"message": "hi"}, headers=bearer(client)).json()
        response = client.post(
            CHAT,
            json={"message": "let me see", "conversation_id": owner["conversation_id"]},
            headers=bearer(client, OTHER_CLIENT_EMAIL),
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "conversation_not_found"


def test_the_authenticated_user_reaches_the_tool_not_an_llm_supplied_identity(make_client):
    """The endpoint's central guarantee. The model names another client; the booking is
    still made for the caller, because identity is resolved before the agent runs."""
    model = scripted(
        [
            (
                "BookViewing",
                {
                    "property_id": "prop-001",
                    "requested_slot_time": "2026-08-08T13:00:00+07:00",
                    "client_id": "u-client-9",
                },
            )
        ],
        "Booked.",
    )
    with make_client(model) as client:
        response = client.post(CHAT, json={"message": "book it"}, headers=bearer(client))
        body = response.json()
        engine = client.app.state.engine

    assert response.status_code == 200, response.text
    assert body["tool_calls"][0]["tool"] == "BookViewing"
    with Session(engine) as session:
        bookings = session.exec(select(Booking)).all()
    created = [b for b in bookings if b.availability_slot_id == "avail-002"]
    assert len(created) == 1
    assert created[0].client_id == "u-client-1"


def test_a_blank_message_is_rejected_by_validation(make_client):
    with make_client(scripted("ok")) as client:
        response = client.post(CHAT, json={"message": ""}, headers=bearer(client))
    assert response.status_code == 422


# ============================================================================
# QA extensions — endpoint behaviors the implementation pass left untested:
# degradation paths, conversation continuity, and per-role reachability.
# ============================================================================

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT_EMAIL = "siti.agent@evdekimi.test"


def always_calls_a_tool() -> FunctionModel:
    """A model stuck in a loop — the pathological run ``UsageLimits`` bounds. Modelled
    on the real incident the Phase 3 live test hit: a misleading error ``guidance`` sent
    the model back to the same tool with the same arguments, five times."""

    def respond(messages, info) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart("SearchProperty", {"city": "Jakarta Selatan"})]
        )

    return FunctionModel(respond)


def raises_mid_run() -> FunctionModel:
    def respond(messages, info) -> ModelResponse:
        raise UnexpectedModelBehavior("the provider returned something unusable")

    return FunctionModel(respond)


# ------------------------------------------------------------------ degradation paths


def test_a_tool_call_loop_is_a_503_not_a_raw_error(make_client):
    """``UsageLimitExceeded`` escaping would surface as a 500 with a stack trace. The
    caller needs "try again", which is what 503 tells the SPA — a 500 reads as a bug and
    a 401 would send the user to re-login."""
    with make_client(always_calls_a_tool()) as client:
        response = client.post(CHAT, json={"message": "find me a flat"}, headers=bearer(client))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "agent_unavailable"
    # No internals: not the tool name, not the limit, not an exception class.
    assert "SearchProperty" not in response.text
    assert "UsageLimitExceeded" not in response.text


def test_an_agent_run_failure_is_a_503_not_a_500(make_client):
    """The provider being unreachable or returning garbage is an availability problem,
    and the same posture applies."""
    with make_client(raises_mid_run()) as client:
        response = client.post(CHAT, json={"message": "hi"}, headers=bearer(client))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "agent_unavailable"
    assert "unusable" not in response.text


def test_a_failed_turn_does_not_persist_a_half_written_transcript(make_client):
    """The turn is persisted after the run, so a failed run must leave the conversation
    exactly as it was — a user message with no reply would replay on the next turn as
    though the assistant had ignored it."""
    with make_client(always_calls_a_tool()) as client:
        client.post(CHAT, json={"message": "find me a flat"}, headers=bearer(client))
        engine = client.app.state.engine

    with Session(engine) as session:
        assert session.exec(select(Message)).all() == []


def test_an_unreadable_stored_history_degrades_instead_of_bricking_the_conversation(
    make_client,
):
    """A history the current Pydantic AI version cannot parse — after a library upgrade,
    say. Dropping context degrades one reply; raising would make the conversation
    permanently unusable, with no way for the user to recover but to notice and start
    over."""
    with make_client(scripted("first", "second")) as client:
        headers = bearer(client)
        first = client.post(CHAT, json={"message": "hi"}, headers=headers).json()
        engine = client.app.state.engine

        with Session(engine) as session:
            conversation = session.get(Conversation, first["conversation_id"])
            conversation.history_json = "{ this is not a serialized message list"
            session.add(conversation)
            session.commit()

        response = client.post(
            CHAT,
            json={"message": "still there?", "conversation_id": first["conversation_id"]},
            headers=headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "second"

    with Session(engine) as session:
        conversation = session.get(Conversation, first["conversation_id"])
    # Repaired on the way out: the next turn starts from a history that parses again,
    # so one bad write does not degrade every subsequent turn.
    assert ModelMessagesTypeAdapter.validate_json(conversation.history_json)


# --------------------------------------------------------------- conversation continuity


def test_the_second_turn_actually_receives_the_first_turns_messages(make_client):
    """``history_json`` being non-empty (already asserted) is not the same as the model
    receiving it. This captures what the model was handed on turn two.

    It is load-bearing beyond politeness: the ``booking_ambiguous`` flow requires the
    *candidate booking ids from a tool result* to survive into the next turn, and the
    assistant's question text does not contain them."""
    seen = []

    def respond(messages, info) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart(f"turn {len(seen)}")])

    with make_client(FunctionModel(respond)) as client:
        headers = bearer(client)
        first = client.post(
            CHAT, json={"message": "my name is Andi"}, headers=headers
        ).json()
        client.post(
            CHAT,
            json={"message": "what did I say?", "conversation_id": first["conversation_id"]},
            headers=headers,
        )

    def texts(messages):
        return [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, (UserPromptPart, TextPart))
        ]

    assert "my name is Andi" not in texts(seen[0])[1:]
    second_turn = texts(seen[1])
    assert "my name is Andi" in second_turn
    assert "turn 1" in second_turn
    assert "what did I say?" in second_turn


def test_a_tool_result_from_an_earlier_turn_survives_into_the_next(make_client):
    """The concrete reason ``history_json`` is persisted alongside the readable
    transcript (checkpoint decision #8): a tool's structured result, not just the prose
    the assistant produced from it."""
    seen = []

    def respond(messages, info) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(
                parts=[ToolCallPart("SearchProperty", {"city": "Jakarta Selatan"})]
            )
        return ModelResponse(parts=[TextPart("here they are")])

    with make_client(FunctionModel(respond)) as client:
        headers = bearer(client)
        first = client.post(CHAT, json={"message": "find flats"}, headers=headers).json()
        client.post(
            CHAT,
            json={"message": "book the first one", "conversation_id": first["conversation_id"]},
            headers=headers,
        )

    tool_returns = [
        part
        for message in seen[-1]
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.tool_name for part in tool_returns] == ["SearchProperty"]


def test_the_readable_transcript_and_the_agent_history_stay_in_step(make_client):
    """Two persistence shapes, deliberately (checkpoint decision #8). They must grow
    together — a transcript that lags the history is what a support engineer reads when
    reconstructing what the user was told."""
    with make_client(scripted("one", "two", "three")) as client:
        headers = bearer(client)
        conversation_id = client.post(
            CHAT, json={"message": "1"}, headers=headers
        ).json()["conversation_id"]
        for turn in ("2", "3"):
            client.post(
                CHAT,
                json={"message": turn, "conversation_id": conversation_id},
                headers=headers,
            )
        engine = client.app.state.engine

    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id)
        messages = session.exec(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()

    assert len(messages) == 6  # three turns, user + assistant each
    history = ModelMessagesTypeAdapter.validate_json(conversation.history_json)
    user_prompts = [
        part.content
        for message in history
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert user_prompts == ["1", "2", "3"]


def test_conversations_are_kept_separate(make_client):
    """One user, two conversations: the second must not inherit the first's history, or
    a user starting fresh would get answers grounded in a session they meant to leave."""
    seen = []

    def respond(messages, info) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart("ok")])

    with make_client(FunctionModel(respond)) as client:
        headers = bearer(client)
        client.post(CHAT, json={"message": "the first secret"}, headers=headers)
        client.post(CHAT, json={"message": "a brand new topic"}, headers=headers)

    contents = [
        part.content
        for message in seen[1]
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert contents == ["a brand new topic"]


# --------------------------------------------------------------------- role reachability


@pytest.mark.parametrize("email", [ADMIN_EMAIL, AGENT_EMAIL, CLIENT_EMAIL])
def test_every_role_can_reach_the_agent(make_client, email):
    """The chat endpoint is open to all three roles (README API surface). Role
    differentiation happens inside the tools, against the authenticated caller — not by
    gating the endpoint, which would remove FAQ and escalation from staff entirely."""
    with make_client(scripted("hello")) as client:
        response = client.post(CHAT, json={"message": "hi"}, headers=bearer(client, email))

    assert response.status_code == 200, response.text


def test_the_authenticated_role_reaches_the_tool_not_a_role_the_model_claims(make_client):
    """An ``agent``-role caller's search returns the agent's own drafts; the same script
    run as a ``client`` does not. Nothing in the request or the model's arguments says
    which — it comes from the JWT."""
    def search_once():
        # A fresh script per client: `scripted` carries a turn counter, so reusing one
        # instance would replay the *second* turn for the second caller. `limit` is raised
        # past the default 10 so the difference is visible rather than clipped — the seed
        # set is 12 active listings plus one `under_offer` belonging to u-agent-1.
        return scripted([("SearchProperty", {"limit": 25})], "found them")

    with make_client(search_once()) as client:
        as_agent = client.post(
            CHAT, json={"message": "list everything"}, headers=bearer(client, AGENT_EMAIL)
        ).json()
    with make_client(search_once()) as client:
        as_client = client.post(
            CHAT, json={"message": "list everything"}, headers=bearer(client, CLIENT_EMAIL)
        ).json()

    agent_count = int(as_agent["tool_calls"][0]["result_summary"].split()[0])
    client_count = int(as_client["tool_calls"][0]["result_summary"].split()[0])
    assert agent_count > client_count


def test_a_conversation_id_that_never_existed_is_also_a_404(make_client):
    """Same code as someone else's conversation, so the two are indistinguishable — the
    posture only works if both sides of it hold."""
    with make_client(scripted("ok")) as client:
        response = client.post(
            CHAT,
            json={"message": "hi", "conversation_id": "conv-never-existed"},
            headers=bearer(client),
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "conversation_not_found"


def test_a_rejected_conversation_id_creates_nothing(make_client):
    """A 404 that still wrote a Conversation row would leak rows on every probe and,
    worse, make the second attempt with the same id succeed."""
    with make_client(scripted("ok")) as client:
        client.post(
            CHAT,
            json={"message": "hi", "conversation_id": "conv-never-existed"},
            headers=bearer(client),
        )
        engine = client.app.state.engine

    with Session(engine) as session:
        assert session.get(Conversation, "conv-never-existed") is None
