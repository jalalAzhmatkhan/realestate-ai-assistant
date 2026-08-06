"""Schema behavior of the RAG observability entities.

Covers the three properties that are real behavior rather than declarations: the
tables accept a fully-populated row, `faithfulness_checks.message_id` is unique at
the database level (the idempotency guarantee in the checkpoint's decision 4), and
the JSON columns round-trip the nested structures the metric readers depend on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.session import build_engine, create_tables
from app.models import (
    Conversation,
    FaithfulnessCheck,
    FaithfulnessClaim,
    Message,
    RetrievalEvalCase,
    RetrievalEvalRun,
    RetrievalLog,
    User,
)

from ..conftest import make_db_settings

JAKARTA = timezone(timedelta(hours=7))

OBSERVABILITY_TABLES = {
    "retrieval_logs",
    "retrieval_eval_runs",
    "retrieval_eval_cases",
    "faithfulness_checks",
    "faithfulness_claims",
}

RESULTS = [
    {"rank": 1, "faq_id": "faq-003", "question": "Deposit?", "category": "deposit", "score": 0.81},
    {"rank": 2, "faq_id": "faq-011", "question": "Utilities?", "category": "utilities", "score": 0.62},
]


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(make_db_settings(tmp_path))
    create_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def turn(engine):
    """A minimal admin -> client -> conversation -> user/assistant message graph."""
    with Session(engine) as session:
        session.add(
            User(
                id="u-admin-o",
                name="Admin",
                email="admin-o@evdekimi.test",
                role="admin",
                hashed_password="$2b$12$notarealhashbutastringisfine",
            )
        )
        session.add(
            User(
                id="u-client-o",
                name="Client",
                email="client-o@evdekimi.test",
                role="client",
                hashed_password="$2b$12$notarealhashbutastringisfine",
            )
        )
        session.flush()
        session.add(Conversation(id="conv-o", user_id="u-client-o"))
        session.flush()
        session.add(
            Message(id="msg-user-o", conversation_id="conv-o", role="user", content="deposit?")
        )
        session.add(
            Message(
                id="msg-asst-o", conversation_id="conv-o", role="assistant", content="One month."
            )
        )
        session.commit()
    return engine


def _log(**overrides) -> RetrievalLog:
    values = {
        "conversation_id": "conv-o",
        "message_id": "msg-asst-o",
        "user_message_id": "msg-user-o",
        "user_id": "u-client-o",
        "query_text": "security deposit amount rental",
        "requested_top_k": 3,
        "effective_top_k": 3,
        "min_score": 0.55,
        "results": RESULTS,
        "result_count": 2,
        "top_score": 0.81,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "latency_ms": 41,
        **overrides,
    }
    return RetrievalLog(**values)


def _run(**overrides) -> RetrievalEvalRun:
    values = {
        "triggered_by_id": "u-admin-o",
        "tiers": ["paraphrase", "identity", "negative"],
        "k_values": [1, 3, 5],
        "eval_set_version": "2026-08-06.1",
        "case_count": 50,
        "graded_case_count": 42,
        "negative_case_count": 8,
        "recall_at_k": {"1": 0.79, "3": 0.93, "5": 0.96},
        "mrr": 0.85,
        "abstention_rate": 0.875,
        "identity_recall_at_1": 1.0,
        "min_score": 0.55,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "index_row_count": 14,
        "index_indexed_at": datetime(2026, 8, 6, 9, 2, 11, tzinfo=JAKARTA),
        "status": "completed",
        "duration_ms": 4820,
        **overrides,
    }
    return RetrievalEvalRun(**values)


def _check(**overrides) -> FaithfulnessCheck:
    values = {
        "message_id": "msg-asst-o",
        "conversation_id": "conv-o",
        "user_id": "u-client-o",
        "status": "scored",
        "score": 0.67,
        "supported_count": 2,
        "unsupported_count": 1,
        "not_applicable_count": 0,
        "claim_count": 3,
        "context_faq_ids": ["faq-003", "faq-011"],
        "judge_model": "gpt-4o-mini",
        "duration_ms": 2611,
        **overrides,
    }
    return FaithfulnessCheck(**values)


# --- schema creation ----------------------------------------------------------


def test_create_all_creates_every_observability_table(engine):
    assert OBSERVABILITY_TABLES <= set(inspect(engine).get_table_names())


@pytest.mark.parametrize("table", ["retrieval_eval_runs", "faithfulness_checks"])
def test_status_is_indexed(engine, table):
    """Both list endpoints filter on `status`, and the codebase indexes every
    filterable enum column (properties/bookings/escalations all do). Asserted
    rather than trusted to the model declaration, which is easy to drop silently."""
    indexed = {
        tuple(index["column_names"]) for index in inspect(engine).get_indexes(table)
    }

    assert ("status",) in indexed


# --- a fully-populated row of each kind ---------------------------------------


def test_retrieval_log_row_inserts(turn):
    with Session(turn) as session:
        session.add(_log(id="rlog-fixed"))
        session.commit()

        stored = session.get(RetrievalLog, "rlog-fixed")
        assert stored.result_count == 2
        assert stored.created_at.tzinfo is not None


def test_eval_run_and_its_cases_insert(turn):
    with Session(turn) as session:
        session.add(_run(id="evalrun-fixed"))
        session.flush()
        session.add(
            RetrievalEvalCase(
                id="evalcase-fixed",
                run_id="evalrun-fixed",
                case_id="eval-007",
                tier="paraphrase",
                query_text="can I bring my cat to the apartment",
                expected_faq_ids=["faq-004"],
                results=RESULTS,
                first_relevant_rank=1,
                reciprocal_rank=1.0,
                recall_at_k={"1": 1.0, "3": 1.0, "5": 1.0},
                abstained=False,
            )
        )
        session.commit()

        assert session.get(RetrievalEvalCase, "evalcase-fixed").run_id == "evalrun-fixed"


def test_faithfulness_check_and_its_claims_insert(turn):
    with Session(turn) as session:
        session.add(_check(id="fcheck-fixed"))
        session.flush()
        session.add(
            FaithfulnessClaim(
                id="fclaim-fixed",
                check_id="fcheck-fixed",
                claim_index=0,
                claim_text="The security deposit is one month's rent.",
                verdict="supported",
                supporting_faq_ids=["faq-003"],
                rationale="Entry faq-003 states one month for unfurnished units.",
            )
        )
        session.commit()

        assert session.get(FaithfulnessClaim, "fclaim-fixed").verdict == "supported"


def test_eval_run_records_the_index_state_that_produced_it(turn):
    """A metric drop caused by a forgotten reindex has to be distinguishable from
    one caused by a worse embedding model, so both are stored per run."""
    with Session(turn) as session:
        session.add(_run(id="evalrun-index"))
        session.commit()

    with Session(turn) as session:
        stored = session.get(RetrievalEvalRun, "evalrun-index")
        assert stored.index_row_count == 14
        assert stored.index_indexed_at == datetime(2026, 8, 6, 9, 2, 11, tzinfo=JAKARTA)
        assert stored.index_indexed_at.utcoffset() == timedelta(0)


def test_an_eval_run_over_an_empty_index_stores_a_null_indexed_at(turn):
    with Session(turn) as session:
        session.add(_run(id="evalrun-noindex", index_row_count=0, index_indexed_at=None))
        session.commit()

    with Session(turn) as session:
        stored = session.get(RetrievalEvalRun, "evalrun-noindex")
        assert stored.index_row_count == 0
        assert stored.index_indexed_at is None


def test_generated_ids_carry_their_prefixes(turn):
    with Session(turn) as session:
        log, run, check = _log(), _run(), _check()
        session.add_all([log, run, check])
        session.commit()

        assert log.id.startswith("rlog-")
        assert run.id.startswith("evalrun-")
        assert check.id.startswith("fcheck-")


# --- the idempotency guarantee ------------------------------------------------


def test_a_second_check_for_the_same_message_is_rejected(turn):
    """One check per assistant message is enforced by the database, not a pre-check:
    two background tasks racing on the same message must not both write a score."""
    with Session(turn) as session:
        session.add(_check(id="fcheck-first"))
        session.commit()

    with Session(turn) as session:
        session.add(_check(id="fcheck-second", status="failed", score=None))
        with pytest.raises(IntegrityError):
            session.commit()


def test_two_checks_for_different_messages_are_allowed(turn):
    """Control for the test above — the constraint must be per-message, not global."""
    with Session(turn) as session:
        session.add(
            Message(id="msg-asst-o2", conversation_id="conv-o", role="assistant", content="Two.")
        )
        session.flush()
        session.add(_check(id="fcheck-a"))
        session.add(_check(id="fcheck-b", message_id="msg-asst-o2"))
        session.commit()

        assert session.get(FaithfulnessCheck, "fcheck-b") is not None


# --- JSON round-trips ---------------------------------------------------------


def test_retrieval_log_results_round_trip_with_nested_dicts(turn):
    with Session(turn) as session:
        session.add(_log(id="rlog-json"))
        session.commit()

    with Session(turn) as session:
        assert session.get(RetrievalLog, "rlog-json").results == RESULTS


def test_empty_results_round_trip_as_an_empty_list(turn):
    """An empty retrieval is a successful "nothing cleared the floor", so `[]` must
    survive the round trip as `[]` and never as null."""
    with Session(turn) as session:
        session.add(_log(id="rlog-empty", results=[], result_count=0, top_score=None))
        session.commit()

    with Session(turn) as session:
        stored = session.get(RetrievalLog, "rlog-empty")
        assert stored.results == []
        assert stored.top_score is None


def test_eval_run_json_metrics_round_trip(turn):
    with Session(turn) as session:
        session.add(_run(id="evalrun-json"))
        session.commit()

    with Session(turn) as session:
        stored = session.get(RetrievalEvalRun, "evalrun-json")
        assert stored.recall_at_k == {"1": 0.79, "3": 0.93, "5": 0.96}
        assert stored.tiers == ["paraphrase", "identity", "negative"]
        assert stored.k_values == [1, 3, 5]


def test_faithfulness_context_faq_ids_round_trip(turn):
    with Session(turn) as session:
        session.add(_check(id="fcheck-json"))
        session.commit()

    with Session(turn) as session:
        assert session.get(FaithfulnessCheck, "fcheck-json").context_faq_ids == [
            "faq-003",
            "faq-011",
        ]


# --- nullability --------------------------------------------------------------


def test_a_failed_check_stores_a_null_score_and_null_counts(turn):
    """`score` is non-null if and only if status == "scored" — a pipeline failure
    must not be storable as a 0.0 that reads like a total hallucination."""
    with Session(turn) as session:
        session.add(
            _check(
                id="fcheck-failed",
                status="failed",
                score=None,
                supported_count=None,
                unsupported_count=None,
                not_applicable_count=None,
                claim_count=None,
                context_faq_ids=[],
                error_code="judging_failed",
                duration_ms=None,
            )
        )
        session.commit()

    with Session(turn) as session:
        stored = session.get(FaithfulnessCheck, "fcheck-failed")
        assert stored.score is None
        assert stored.claim_count is None
        assert stored.error_code == "judging_failed"


def test_a_failed_eval_run_stores_null_metrics(turn):
    with Session(turn) as session:
        session.add(
            _run(
                id="evalrun-failed",
                status="failed",
                recall_at_k=None,
                mrr=None,
                abstention_rate=None,
                identity_recall_at_1=None,
                error_code="eval_run_failed",
            )
        )
        session.commit()

    with Session(turn) as session:
        stored = session.get(RetrievalEvalRun, "evalrun-failed")
        assert (stored.recall_at_k, stored.mrr, stored.abstention_rate) == (None, None, None)


def test_a_retrieval_log_without_a_triggering_user_message_is_allowed(turn):
    with Session(turn) as session:
        session.add(_log(id="rlog-nouser", user_message_id=None))
        session.commit()

        assert session.get(RetrievalLog, "rlog-nouser").user_message_id is None


def test_a_claim_without_a_rationale_is_allowed(turn):
    with Session(turn) as session:
        session.add(_check(id="fcheck-norat"))
        session.flush()
        session.add(
            FaithfulnessClaim(
                id="fclaim-norat",
                check_id="fcheck-norat",
                claim_index=0,
                claim_text="Viewings are free.",
                verdict="not_applicable",
                supporting_faq_ids=[],
                rationale=None,
            )
        )
        session.commit()

        assert session.get(FaithfulnessClaim, "fclaim-norat").rationale is None


# --- foreign keys -------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["conversation_id", "message_id", "user_message_id", "user_id"]
)
def test_a_retrieval_log_with_a_dangling_reference_is_rejected(turn, field):
    with Session(turn) as session:
        session.add(_log(id="rlog-bad", **{field: "does-not-exist"}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_an_eval_case_with_a_dangling_run_is_rejected(turn):
    with Session(turn) as session:
        session.add(
            RetrievalEvalCase(
                id="evalcase-bad",
                run_id="evalrun-nope",
                case_id="eval-001",
                tier="paraphrase",
                query_text="q",
                expected_faq_ids=[],
                results=[],
                first_relevant_rank=None,
                reciprocal_rank=0.0,
                recall_at_k={},
                abstained=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_claim_with_a_dangling_check_is_rejected(turn):
    with Session(turn) as session:
        session.add(
            FaithfulnessClaim(
                id="fclaim-bad",
                check_id="fcheck-nope",
                claim_index=0,
                claim_text="c",
                verdict="unsupported",
                supporting_faq_ids=[],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# --- timestamps ---------------------------------------------------------------


def test_created_at_defaults_are_utc_aware(turn):
    with Session(turn) as session:
        session.add(_log(id="rlog-tz"))
        session.add(_run(id="evalrun-tz"))
        session.add(_check(id="fcheck-tz"))
        session.commit()

    with Session(turn) as session:
        for model, row_id in (
            (RetrievalLog, "rlog-tz"),
            (RetrievalEvalRun, "evalrun-tz"),
            (FaithfulnessCheck, "fcheck-tz"),
        ):
            created_at = session.get(model, row_id).created_at
            assert created_at.tzinfo is not None
            assert created_at.utcoffset() == timezone.utc.utcoffset(None)
