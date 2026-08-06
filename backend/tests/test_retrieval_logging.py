"""Retrieval logging: every ``search_faq`` call becomes a ``retrieval_logs`` row.

The tool itself writes nothing — it appends to an in-run accumulator that
``app/api/chat.py`` drains into the turn's own transaction, so a log row cannot exist
without the assistant message it explains, and cannot cost an extra round trip.

These tests drive the real endpoint with a scripted model, which is the only way to
assert the part that matters: the row references the *assistant* message produced by the
same turn, and a logging fault degrades to a missing row rather than a failed chat.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent.orchestrator import build_agent
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import Message, RetrievalLog, User
from app.rag.index import FaqEntry, FaqIndex

from .conftest import SEED_PASSWORD, make_db_settings
from .test_agent import RecordingNotifier, StubEmbeddingModel
from .test_chat_api import CHAT, CLIENT_EMAIL, LOGIN, scripted


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
            )
        ],
    )


@pytest.fixture
def make_client(tmp_path, faq_index):
    def _make(model, **setting_overrides):
        settings = make_db_settings(tmp_path, **setting_overrides)
        create_tables(build_engine(settings))
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


def rows(engine, model=RetrievalLog) -> list:
    with Session(engine) as session:
        return list(session.exec(select(model)).all())


def searching(query: str, *replies, top_k: int | None = None):
    args = {"query": query}
    if top_k is not None:
        args["top_k"] = top_k
    return scripted([("search_faq", args)], *(replies or ("Here is what I found.",)))


def test_a_search_faq_call_writes_a_retrieval_log_row(make_client):
    with make_client(searching("deposit", top_k=2)) as client:
        response = client.post(
            CHAT, json={"message": "how much deposit?"}, headers=bearer(client)
        )
        engine = client.app.state.engine

    assert response.status_code == 200, response.text
    (log,) = rows(engine)
    assert log.query_text == "deposit"
    assert log.requested_top_k == 2
    assert log.effective_top_k == 2
    assert log.min_score == pytest.approx(0.55)
    assert log.result_count == 1
    assert log.top_score is not None and log.top_score >= 0.55
    assert log.embedding_model == "stub-embeddings"
    assert log.latency_ms >= 0


def test_the_row_carries_ranked_results_with_one_based_ranks(make_client):
    with make_client(searching("deposit")) as client:
        client.post(CHAT, json={"message": "deposit?"}, headers=bearer(client))
        engine = client.app.state.engine

    (log,) = rows(engine)
    assert log.results == [
        {
            "rank": 1,
            "faq_id": "faq-deposit",
            "question": "How much is the deposit?",
            "category": "fees",
            "score": pytest.approx(log.top_score),
        }
    ]


def test_the_row_is_tied_to_the_turn_that_produced_it(make_client):
    """The assistant message is the anchor — a retrieval is only interpretable next to
    the answer it fed."""
    with make_client(searching("deposit", "The deposit is one month's rent.")) as client:
        body = client.post(
            CHAT, json={"message": "deposit?"}, headers=bearer(client)
        ).json()
        engine = client.app.state.engine

    (log,) = rows(engine)
    messages = {message.role: message for message in rows(engine, Message)}
    (caller,) = [user for user in rows(engine, User) if user.email == CLIENT_EMAIL]
    assert log.conversation_id == body["conversation_id"]
    assert log.message_id == messages["assistant"].id
    assert log.user_message_id == messages["user"].id
    assert log.user_id == caller.id


def test_an_empty_result_search_still_writes_a_row(make_client):
    """The highest-signal row of all: the knowledge base was asked and had nothing."""
    with make_client(searching("nothing in this corpus")) as client:
        response = client.post(
            CHAT, json={"message": "who owns the moon?"}, headers=bearer(client)
        )
        engine = client.app.state.engine

    assert response.status_code == 200, response.text
    (log,) = rows(engine)
    assert log.result_count == 0
    assert log.results == []
    assert log.top_score is None


def test_each_search_in_a_turn_gets_its_own_row(make_client):
    model = scripted(
        [("search_faq", {"query": "deposit"})],
        [("search_faq", {"query": "pet"})],
        "Both answered.",
    )
    with make_client(model) as client:
        client.post(CHAT, json={"message": "two questions"}, headers=bearer(client))
        engine = client.app.state.engine

    logged = rows(engine)
    assert [log.query_text for log in logged] == ["deposit", "pet"]
    assert len({log.message_id for log in logged}) == 1


def test_a_turn_without_a_search_writes_no_rows(make_client):
    with make_client(scripted("Hello.")) as client:
        client.post(CHAT, json={"message": "hi"}, headers=bearer(client))
        engine = client.app.state.engine

    assert rows(engine) == []


def test_no_rows_are_written_when_retrieval_logging_is_disabled(make_client):
    with make_client(searching("deposit"), retrieval_log_enabled=False) as client:
        response = client.post(
            CHAT, json={"message": "deposit?"}, headers=bearer(client)
        )
        engine = client.app.state.engine

    assert response.status_code == 200, response.text
    assert rows(engine) == []
    # The turn itself is untouched by the switch.
    assert len(rows(engine, Message)) == 2


def test_the_tool_output_is_unchanged_by_the_instrumentation(make_client):
    """Purely additive: nothing about what the model sees changes."""
    with make_client(searching("deposit")) as client:
        body = client.post(
            CHAT, json={"message": "deposit?"}, headers=bearer(client)
        ).json()

    assert body["tool_calls"][0]["tool"] == "search_faq"
    assert body["tool_calls"][0]["result_summary"] == "1 result(s)"


def test_a_failing_log_row_does_not_fail_the_chat_turn(make_client, monkeypatch):
    """Observability is never allowed to cost the user their answer."""

    def explode(**_kwargs):
        raise ValueError("malformed retrieval record")

    monkeypatch.setattr("app.api.chat.RetrievalLog", explode)

    with make_client(searching("deposit", "The deposit is one month's rent.")) as client:
        response = client.post(
            CHAT, json={"message": "deposit?"}, headers=bearer(client)
        )
        engine = client.app.state.engine

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "The deposit is one month's rent."
    assert len(rows(engine, Message)) == 2
    assert rows(engine) == []
