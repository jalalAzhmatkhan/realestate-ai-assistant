"""``/api/v1/observability`` — admin-only retrieval logs, evaluation runs, faithfulness.

Contract: ``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md``
decision 6. Priorities here, given the risk each carries: role-level 403 (these tables
carry cross-conversation query text), the headline metric excluding the ``identity``
tier, the ``faq_index_unavailable`` guard, and ``NULLS LAST`` on the one nullable sort
column anywhere in the API.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import Conversation, FaithfulnessCheck, Message, RetrievalLog, User
from app.rag.index import load_faq_entries

from .conftest import make_db_settings
from .rag_doubles import InMemoryFaqIndex
from .test_agent import StubEmbeddingModel
from .test_properties_api import bearer, code_of

OBS = "/api/v1/observability"
LOGS = f"{OBS}/retrieval-logs"
EVAL_RUNS = f"{OBS}/retrieval/eval-runs"
FAITHFULNESS = f"{OBS}/faithfulness"

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT_EMAIL = "siti.agent@evdekimi.test"
CLIENT_EMAIL = "andi.client@evdekimi.test"


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def faq_index(settings):
    return InMemoryFaqIndex(StubEmbeddingModel(), load_faq_entries(settings.seed_data_dir))


@pytest.fixture
def client(settings, faq_index):
    with TestClient(create_app(settings, faq_index=faq_index)) as test_client:
        yield test_client


def seeded_turn(engine, *, email: str = CLIENT_EMAIL) -> dict:
    """A real conversation + user/assistant message pair, satisfying the FK
    constraints ``retrieval_logs``/``faithfulness_checks`` enforce (SQLite runs with
    ``PRAGMA foreign_keys=ON`` here, same as production)."""
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        conversation = Conversation(id=f"conv-{uuid4().hex[:8]}", user_id=user.id)
        session.add(conversation)
        session.commit()

        user_message = Message(
            id=f"msg-{uuid4().hex[:8]}", conversation_id=conversation.id, role="user", content="hi"
        )
        session.add(user_message)
        session.commit()

        assistant_message = Message(
            id=f"msg-{uuid4().hex[:8]}",
            conversation_id=conversation.id,
            role="assistant",
            content="The deposit is one month's rent, which is quite a long sentence to truncate.",
        )
        session.add(assistant_message)
        session.commit()

        return {
            "user_id": user.id,
            "conversation_id": conversation.id,
            "user_message_id": user_message.id,
            "message_id": assistant_message.id,
        }


def seed_retrieval_log(engine, turn: dict, **overrides) -> RetrievalLog:
    log = RetrievalLog(
        conversation_id=turn["conversation_id"],
        message_id=turn["message_id"],
        user_message_id=turn["user_message_id"],
        user_id=turn["user_id"],
        query_text=overrides.get("query_text", "how much is the deposit"),
        requested_top_k=3,
        effective_top_k=3,
        min_score=0.55,
        results=overrides.get("results", []),
        result_count=len(overrides.get("results", [])),
        top_score=overrides.get("top_score"),
        embedding_model="stub-embedding-model",
        latency_ms=12,
    )
    with Session(engine) as session:
        session.add(log)
        session.commit()
        session.refresh(log)
    return log


def seed_faithfulness_check(engine, turn: dict, **overrides) -> FaithfulnessCheck:
    check = FaithfulnessCheck(
        message_id=turn["message_id"],
        conversation_id=turn["conversation_id"],
        user_id=turn["user_id"],
        status=overrides.get("status", "scored"),
        score=overrides.get("score"),
        supported_count=overrides.get("supported_count"),
        unsupported_count=overrides.get("unsupported_count"),
        not_applicable_count=overrides.get("not_applicable_count"),
        claim_count=overrides.get("claim_count"),
        context_faq_ids=overrides.get("context_faq_ids", []),
        judge_model="stub-judge-model",
        error_code=overrides.get("error_code"),
        duration_ms=overrides.get("duration_ms"),
    )
    with Session(engine) as session:
        session.add(check)
        session.commit()
        session.refresh(check)
    return check


# --- role-level denial -------------------------------------------------------------


@pytest.mark.parametrize("email", [AGENT_EMAIL, CLIENT_EMAIL])
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", LOGS),
        ("get", EVAL_RUNS),
        ("post", EVAL_RUNS),
        ("get", FAITHFULNESS),
    ],
)
def test_non_admin_gets_403_not_404(client, email, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    response = getattr(client, method)(path, headers=bearer(client, email), **kwargs)
    assert response.status_code == 403, response.text
    assert code_of(response) == "forbidden"


def test_admin_can_reach_every_list_endpoint(client):
    for path in (LOGS, EVAL_RUNS, FAITHFULNESS):
        response = client.get(path, headers=bearer(client, ADMIN_EMAIL))
        assert response.status_code == 200, response.text


# --- retrieval logs ------------------------------------------------------------


def test_retrieval_logs_lists_and_filters_by_has_results(client):
    engine = client.app.state.engine
    turn = seeded_turn(engine)
    seed_retrieval_log(
        engine,
        turn,
        query_text="deposit amount",
        results=[{"rank": 1, "faq_id": "faq-001", "question": "q", "category": "fees", "score": 0.9}],
        top_score=0.9,
    )
    seed_retrieval_log(engine, turn, query_text="weather in bali", results=[])

    response = client.get(LOGS, headers=bearer(client, ADMIN_EMAIL), params={"has_results": "false"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["results"][0]["query_text"] == "weather in bali"
    assert body["results"][0]["results"] == []


def test_retrieval_logs_free_text_filters_on_query_text(client):
    engine = client.app.state.engine
    turn = seeded_turn(engine)
    seed_retrieval_log(engine, turn, query_text="deposit amount")
    seed_retrieval_log(engine, turn, query_text="pet policy")

    response = client.get(LOGS, headers=bearer(client, ADMIN_EMAIL), params={"q": "deposit"})
    assert response.json()["total"] == 1


def test_retrieval_logs_invalid_date_range_is_422(client):
    response = client.get(
        LOGS,
        headers=bearer(client, ADMIN_EMAIL),
        params={"date_from": "2026-08-06T00:00:00Z", "date_to": "2026-08-01T00:00:00Z"},
    )
    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_date_range"


# --- evaluation runs -------------------------------------------------------------


def test_eval_run_scores_paraphrase_and_reports_identity_and_negative_separately(client):
    response = client.post(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={})
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["recall_at_k"] is not None
    assert set(body["recall_at_k"]) == {"1", "3", "5"}
    assert body["mrr"] is not None
    # The identity canary is reported separately and is not folded into
    # graded_case_count, which must equal the paraphrase tier's size alone.
    assert body["identity_recall_at_1"] is not None
    assert body["graded_case_count"] < body["case_count"]
    assert body["negative_case_count"] > 0
    assert body["abstention_rate"] is not None
    assert len(body["cases"]) == body["case_count"]
    assert body["index_row_count"] == len(load_faq_entries(client.app.state.settings.seed_data_dir))


def test_eval_run_can_be_scoped_to_one_tier(client):
    response = client.post(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={"tiers": ["identity"]})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["case_count"] == len(load_faq_entries(client.app.state.settings.seed_data_dir))
    # Identity is never a graded/headline case even when it's the only tier requested.
    assert body["graded_case_count"] == 0
    assert body["recall_at_k"] is None
    assert body["negative_case_count"] == 0


def test_eval_run_rejects_invalid_k_value(client):
    response = client.post(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={"k_values": [0]})
    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_k_value"


def test_eval_run_rejects_unknown_tier(client):
    response = client.post(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={"tiers": ["bogus"]})
    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_tier"


def test_eval_run_against_an_empty_index_is_503_not_a_misleading_zero(settings):
    empty_index = InMemoryFaqIndex(StubEmbeddingModel(), [])
    with TestClient(create_app(settings, faq_index=empty_index)) as client:
        response = client.post(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={})
    assert response.status_code == 503, response.text
    assert code_of(response) == "faq_index_unavailable"


def test_eval_run_history_and_detail_round_trip(client):
    created = client.post(
        EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL), json={"tiers": ["negative"]}
    ).json()

    listed = client.get(EVAL_RUNS, headers=bearer(client, ADMIN_EMAIL))
    assert listed.status_code == 200
    assert any(row["id"] == created["id"] for row in listed.json()["results"])
    # The summary (list) item type carries no `cases` key at all.
    assert "cases" not in listed.json()["results"][0]

    detail = client.get(f"{EVAL_RUNS}/{created['id']}", headers=bearer(client, ADMIN_EMAIL))
    assert detail.status_code == 200
    assert len(detail.json()["cases"]) == created["case_count"]


def test_eval_run_detail_unknown_id_is_404(client):
    response = client.get(f"{EVAL_RUNS}/evalrun-does-not-exist", headers=bearer(client, ADMIN_EMAIL))
    assert response.status_code == 404, response.text
    assert code_of(response) == "eval_run_not_found"


# --- faithfulness ----------------------------------------------------------------


def test_faithfulness_list_includes_answer_preview_and_excludes_null_scores_by_range(client):
    engine = client.app.state.engine
    scored_turn = seeded_turn(engine)
    no_context_turn = seeded_turn(engine)

    seed_faithfulness_check(
        engine, scored_turn, score=0.8, status="scored", claim_count=2, supported_count=2, unsupported_count=0
    )
    seed_faithfulness_check(engine, no_context_turn, score=None, status="no_context")

    response = client.get(FAITHFULNESS, headers=bearer(client, ADMIN_EMAIL))
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert len(results) == 2
    scored = next(r for r in results if r["status"] == "scored")
    assert scored["answer_preview"].startswith("The deposit is one month's rent")

    # A min_score/max_score filter excludes the null-score row, not just rows below it.
    ranged = client.get(FAITHFULNESS, headers=bearer(client, ADMIN_EMAIL), params={"min_score": 0.0})
    assert ranged.json()["total"] == 1


def test_faithfulness_score_sort_puts_nulls_last_both_directions(client):
    engine = client.app.state.engine
    seed_faithfulness_check(engine, seeded_turn(engine), score=0.9, status="scored")
    seed_faithfulness_check(engine, seeded_turn(engine), score=None, status="no_context")
    seed_faithfulness_check(engine, seeded_turn(engine), score=0.2, status="scored")

    ascending = client.get(FAITHFULNESS, headers=bearer(client, ADMIN_EMAIL), params={"sort": "score"})
    assert [r["score"] for r in ascending.json()["results"]] == [0.2, 0.9, None]

    descending = client.get(FAITHFULNESS, headers=bearer(client, ADMIN_EMAIL), params={"sort": "-score"})
    assert [r["score"] for r in descending.json()["results"]] == [0.9, 0.2, None]


def test_faithfulness_invalid_score_range_is_422(client):
    response = client.get(
        FAITHFULNESS, headers=bearer(client, ADMIN_EMAIL), params={"min_score": 0.9, "max_score": 0.1}
    )
    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_score_range"


def test_faithfulness_detail_resolves_context_from_faq_embeddings_not_the_file(client, faq_index):
    entry = faq_index._entries[0]
    check = seed_faithfulness_check(
        client.app.state.engine,
        seeded_turn(client.app.state.engine),
        context_faq_ids=[entry.id],
        claim_count=1,
        supported_count=1,
    )

    response = client.get(f"{FAITHFULNESS}/{check.id}", headers=bearer(client, ADMIN_EMAIL))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context"] == [
        {"faq_id": entry.id, "question": entry.question, "category": entry.category, "answer": entry.answer}
    ]


def test_faithfulness_detail_unknown_id_is_404(client):
    response = client.get(f"{FAITHFULNESS}/fcheck-does-not-exist", headers=bearer(client, ADMIN_EMAIL))
    assert response.status_code == 404, response.text
    assert code_of(response) == "faithfulness_check_not_found"
