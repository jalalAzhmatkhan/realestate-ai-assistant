"""Answer faithfulness: the post-response grounding check, and every way it can fail.

The point of these tests is not the happy path — it is the rule the checkpoint states and
QA enforces: **every failure degrades to a missing datum, never a fabricated number.** So
most of what follows drives a broken judge (raises, returns the wrong number of verdicts,
returns duplicate indices) and asserts the same three things each time: `status="failed"`,
`score=None`, the right `error_code`, and a chat turn that succeeded anyway.

Everything runs through the real endpoint with a scripted agent *and* a scripted judge, so
the wiring under test is the real one: the check is scheduled as a background task, opens
its own session, and reads a `retrieval_sink` populated by a real `search_faq` call.
Starlette runs background tasks before the ASGI call completes, so by the time
`client.post` returns, the check has finished.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlmodel import Session, select

from app.agent.orchestrator import build_agent
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import (
    Conversation,
    FaithfulnessCheck,
    FaithfulnessClaim,
    Message,
    User,
)
from app.observability import faithfulness
from app.observability.faithfulness import ContextEntry, run_faithfulness_check
from app.rag.index import FaqEntry

from .conftest import SEED_PASSWORD, make_db_settings
from .rag_doubles import InMemoryFaqIndex
from .test_agent import RecordingNotifier, StubEmbeddingModel
from .test_chat_api import CHAT, CLIENT_EMAIL, LOGIN, scripted

DEPOSIT = FaqEntry(
    id="faq-deposit",
    question="How much is the deposit?",
    answer="The deposit is one month's rent.",
    category="fees",
    tags=("deposit",),
)
PET = FaqEntry(
    id="faq-pet",
    question="Are pets allowed?",
    answer="Cats are allowed; dogs are not.",
    category="policy",
    tags=("pet",),
)


class JudgeUnreachable(RuntimeError):
    """Stands in for a timeout, a 500, or a severed connection to the judge provider."""


def judging(*payloads):
    """A model that answers each structured-output request with the next payload.

    A payload that is an exception is raised instead, which is how a provider failure is
    injected: an unreachable judge and a judge that times out are the same thing here.
    """
    state = {"index": 0}

    def respond(_messages, info) -> ModelResponse:
        payload = payloads[min(state["index"], len(payloads) - 1)]
        state["index"] += 1
        if isinstance(payload, Exception):
            raise payload
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


def claims(*texts):
    return {"claims": list(texts)}


def verdicts(*entries):
    """`entries` are (index, verdict, [supporting ids], rationale) tuples."""
    return {
        "judgments": [
            {
                "claim_index": index,
                "verdict": verdict,
                "supporting_faq_ids": list(supporting),
                "rationale": rationale,
            }
            for index, verdict, supporting, rationale in entries
        ]
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def faq_index():
    return InMemoryFaqIndex(StubEmbeddingModel(), [DEPOSIT, PET])


@pytest.fixture
def make_client(tmp_path, faq_index):
    def _make(agent_model, judge=None, **setting_overrides):
        settings = make_db_settings(tmp_path, **setting_overrides)
        create_tables(build_engine(settings))
        app = create_app(
            settings,
            agent=build_agent(agent_model),
            faq_index=faq_index,
            notifier=RecordingNotifier(),
            judge_model=judge,
        )
        return TestClient(app)

    return _make


def bearer(client) -> dict:
    response = client.post(LOGIN, json={"email": CLIENT_EMAIL, "password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def rows(engine, model=FaithfulnessCheck) -> list:
    with Session(engine) as session:
        return list(session.exec(select(model)).all())


def searching(query: str, reply: str):
    return scripted([("search_faq", {"query": query})], reply)


def run_turn(make_client, agent_model, judge, message="how much deposit?", **overrides):
    """Drive one chat turn and hand back the response body plus the engine."""
    with make_client(agent_model, judge, **overrides) as client:
        response = client.post(CHAT, json={"message": message}, headers=bearer(client))
        engine = client.app.state.engine
    assert response.status_code == 200, response.text
    return response, engine


def check_of(engine) -> FaithfulnessCheck:
    (check,) = rows(engine)
    return check


def assert_missing_datum(check: FaithfulnessCheck, error_code: str) -> None:
    """The single rule this whole pipeline exists to honour."""
    assert check.status == "failed"
    assert check.score is None
    assert check.error_code == error_code


# --- the scored path -------------------------------------------------------------


def test_a_partially_grounded_reply_is_scored_over_its_checkable_claims(make_client):
    judge = judging(
        claims("The deposit is one month's rent.", "The deposit is refundable."),
        verdicts(
            (0, "supported", ["faq-deposit"], "The entry states exactly this."),
            (1, "unsupported", [], "No entry mentions refundability."),
        ),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "It is one month's rent, and refundable."), judge
    )

    check = check_of(engine)
    assert check.status == "scored"
    assert check.score == pytest.approx(0.5)
    assert (check.supported_count, check.unsupported_count) == (1, 1)
    assert check.not_applicable_count == 0
    assert check.claim_count == 2
    assert check.context_faq_ids == ["faq-deposit"]
    assert "function" in check.judge_model
    assert check.duration_ms is not None and check.duration_ms >= 0


def test_the_claims_are_persisted_in_order_with_their_verdicts(make_client):
    judge = judging(
        claims("The deposit is one month's rent.", "The deposit is refundable."),
        verdicts(
            (1, "unsupported", [], "Not stated."),
            (0, "supported", ["faq-deposit"], "Stated verbatim."),
        ),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "One month's rent, refundable."), judge
    )

    persisted = sorted(rows(engine, FaithfulnessClaim), key=lambda claim: claim.claim_index)
    assert [claim.claim_text for claim in persisted] == [
        "The deposit is one month's rent.",
        "The deposit is refundable.",
    ]
    assert [claim.verdict for claim in persisted] == ["supported", "unsupported"]
    assert persisted[0].supporting_faq_ids == ["faq-deposit"]
    assert persisted[1].supporting_faq_ids == []
    assert persisted[1].rationale == "Not stated."


def test_a_fully_grounded_reply_scores_one(make_client):
    judge = judging(
        claims("The deposit is one month's rent."),
        verdicts((0, "supported", ["faq-deposit"], None)),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "The deposit is one month's rent."), judge
    )

    check = check_of(engine)
    assert check.status == "scored"
    assert check.score == pytest.approx(1.0)


def test_a_wholly_ungrounded_reply_scores_zero_which_is_a_real_score(make_client):
    """0.0 is the most important number this pipeline can produce — it must never be
    confused with "not computed", which is null."""
    judge = judging(
        claims("The deposit is three months' rent.", "Deposits are waived in December."),
        verdicts(
            (0, "unsupported", [], "Contradicts the entry."),
            (1, "unsupported", [], "Not mentioned anywhere."),
        ),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "Three months, waived in December."), judge
    )

    check = check_of(engine)
    assert check.status == "scored"
    assert check.score == 0.0
    assert check.score is not None


def test_not_applicable_claims_are_excluded_from_both_sides_of_the_ratio(make_client):
    judge = judging(
        claims(
            "The deposit is one month's rent.",
            "Your viewing is confirmed for Saturday at 10am.",
        ),
        verdicts(
            (0, "supported", ["faq-deposit"], None),
            (1, "not_applicable", [], "A booking detail, not a policy claim."),
        ),
    )
    _, engine = run_turn(
        make_client,
        searching("deposit", "One month's rent. Your viewing is confirmed."),
        judge,
    )

    check = check_of(engine)
    assert check.status == "scored"
    assert check.score == pytest.approx(1.0)
    assert (check.supported_count, check.unsupported_count, check.not_applicable_count) == (
        1,
        0,
        1,
    )


def test_a_judge_citing_an_entry_it_was_never_shown_has_it_dropped(make_client):
    judge = judging(
        claims("The deposit is one month's rent."),
        verdicts((0, "supported", ["faq-deposit", "faq-invented"], None)),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "One month's rent."), judge
    )

    (claim,) = rows(engine, FaithfulnessClaim)
    assert claim.supporting_faq_ids == ["faq-deposit"]


def test_a_long_rationale_is_truncated_rather_than_failing_the_check(make_client):
    judge = judging(
        claims("The deposit is one month's rent."),
        verdicts((0, "supported", ["faq-deposit"], "x" * 900)),
    )
    _, engine = run_turn(make_client, searching("deposit", "One month's rent."), judge)

    (claim,) = rows(engine, FaithfulnessClaim)
    assert check_of(engine).status == "scored"
    assert len(claim.rationale) == 500
    assert claim.rationale.endswith("...")


def test_the_context_is_the_deduplicated_union_of_every_search_in_the_turn(make_client):
    agent_model = scripted(
        [("search_faq", {"query": "deposit"})],
        [("search_faq", {"query": "pet"})],
        [("search_faq", {"query": "deposit"})],
        "Deposit is one month's rent and cats are fine.",
    )
    judge = judging(
        claims("The deposit is one month's rent.", "Cats are allowed."),
        verdicts(
            (0, "supported", ["faq-deposit"], None),
            (1, "supported", ["faq-pet"], None),
        ),
    )
    _, engine = run_turn(make_client, agent_model, judge, message="deposit and pets?")

    check = check_of(engine)
    assert check.context_faq_ids == ["faq-deposit", "faq-pet"]
    assert check.score == pytest.approx(1.0)


# --- the paths that produce no score, and are not failures ------------------------


def test_a_search_that_found_nothing_is_no_context_not_a_zero(make_client):
    """The agent correctly saying "I have no confirmed answer" is not a hallucination."""
    judge = judging(claims("should never be requested"))
    _, engine = run_turn(
        make_client,
        searching("parking", "I don't have a confirmed answer on that."),
        judge,
        message="who owns the moon?",
    )

    check = check_of(engine)
    assert check.status == "no_context"
    assert check.score is None
    assert check.error_code is None
    assert check.context_faq_ids == []
    assert check.claim_count is None
    assert rows(engine, FaithfulnessClaim) == []


def test_a_turn_that_never_searched_the_faq_gets_no_row_at_all(make_client):
    judge = judging(claims("should never be requested"))
    _, engine = run_turn(make_client, scripted("Hello."), judge, message="hi")

    assert rows(engine) == []


def test_a_reply_that_asserts_nothing_has_no_checkable_claims(make_client):
    judge = judging(claims())
    _, engine = run_turn(
        make_client, searching("deposit", "Happy to help — anything else?"), judge
    )

    check = check_of(engine)
    assert check.status == "no_checkable_claims"
    assert check.score is None
    assert check.error_code is None
    assert check.claim_count == 0
    assert rows(engine, FaithfulnessClaim) == []


def test_an_all_not_applicable_reply_has_no_checkable_claims(make_client):
    """Nothing to be faithful *to* — the denominator is zero, so there is no score."""
    judge = judging(
        claims("Your viewing is confirmed for Saturday.", "Your reference is BK-12."),
        verdicts(
            (0, "not_applicable", [], "Booking detail."),
            (1, "not_applicable", [], "Booking detail."),
        ),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "Confirmed for Saturday, ref BK-12."), judge
    )

    check = check_of(engine)
    assert check.status == "no_checkable_claims"
    assert check.score is None
    assert check.not_applicable_count == 2
    assert len(rows(engine, FaithfulnessClaim)) == 2


# --- the failure modes ------------------------------------------------------------


def test_a_failing_decomposition_call_is_a_missing_datum(make_client):
    judge = judging(JudgeUnreachable("connection reset"))
    _, engine = run_turn(make_client, searching("deposit", "One month's rent."), judge)

    assert_missing_datum(check_of(engine), "decomposition_failed")
    assert rows(engine, FaithfulnessClaim) == []


def test_a_decomposition_that_runs_away_past_the_claim_ceiling_fails(make_client):
    judge = judging(claims("a", "b", "c"))
    _, engine = run_turn(
        make_client,
        searching("deposit", "One month's rent."),
        judge,
        faithfulness_max_claims=2,
    )

    assert_missing_datum(check_of(engine), "decomposition_failed")


def test_a_failing_judge_call_is_a_missing_datum(make_client):
    judge = judging(claims("The deposit is one month's rent."), JudgeUnreachable("504"))
    _, engine = run_turn(make_client, searching("deposit", "One month's rent."), judge)

    assert_missing_datum(check_of(engine), "judging_failed")
    assert rows(engine, FaithfulnessClaim) == []


def test_too_few_verdicts_fails_the_whole_check_rather_than_scoring_a_subset(make_client):
    """A partial score is a fabricated score: it answers a question about a claim set
    nobody chose."""
    judge = judging(
        claims("The deposit is one month's rent.", "The deposit is refundable."),
        verdicts((0, "supported", ["faq-deposit"], None)),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "One month's rent, refundable."), judge
    )

    assert_missing_datum(check_of(engine), "malformed_judgment")
    assert rows(engine, FaithfulnessClaim) == []


def test_duplicate_claim_indices_fail_the_whole_check(make_client):
    judge = judging(
        claims("The deposit is one month's rent.", "The deposit is refundable."),
        verdicts(
            (0, "supported", ["faq-deposit"], None),
            (0, "unsupported", [], None),
        ),
    )
    _, engine = run_turn(
        make_client, searching("deposit", "One month's rent, refundable."), judge
    )

    assert_missing_datum(check_of(engine), "malformed_judgment")


def test_an_out_of_range_claim_index_fails_the_whole_check(make_client):
    judge = judging(
        claims("The deposit is one month's rent."),
        verdicts((7, "supported", ["faq-deposit"], None)),
    )
    _, engine = run_turn(make_client, searching("deposit", "One month's rent."), judge)

    assert_missing_datum(check_of(engine), "malformed_judgment")


def test_an_unexpected_internal_error_becomes_a_failed_row_not_a_failed_turn(
    make_client, monkeypatch
):
    """The background task is its own blast radius: nothing escapes it into the turn."""

    async def explode(*_args, **_kwargs):
        raise ZeroDivisionError("something nobody anticipated")

    monkeypatch.setattr(faithfulness, "_evaluate", explode)

    response, engine = run_turn(
        make_client,
        searching("deposit", "The deposit is one month's rent."),
        judging(claims("unused")),
    )

    assert response.json()["reply"] == "The deposit is one month's rent."
    assert_missing_datum(check_of(engine), "internal_error")
    assert len(rows(engine, Message)) == 2


def test_a_check_that_cannot_be_persisted_never_fails_the_turn(make_client, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(faithfulness, "_persist", explode)

    response, engine = run_turn(
        make_client,
        searching("deposit", "The deposit is one month's rent."),
        judging(
            claims("The deposit is one month's rent."),
            verdicts((0, "supported", ["faq-deposit"], None)),
        ),
    )

    assert response.json()["reply"] == "The deposit is one month's rent."
    assert rows(engine) == []


def test_a_judge_model_that_cannot_be_built_never_fails_the_turn(make_client):
    """`judge_model=None` with no API key: the check is skipped, the answer is not."""
    response, engine = run_turn(
        make_client, searching("deposit", "The deposit is one month's rent."), None
    )

    assert response.json()["reply"] == "The deposit is one month's rent."
    assert rows(engine) == []


# --- the invariant, the switch, and idempotency -----------------------------------


@pytest.mark.parametrize(
    "judge, expected_status",
    [
        (
            judging(
                claims("The deposit is one month's rent."),
                verdicts((0, "supported", ["faq-deposit"], None)),
            ),
            "scored",
        ),
        (
            judging(
                claims("The deposit is three months."),
                verdicts((0, "unsupported", [], None)),
            ),
            "scored",
        ),
        (judging(claims()), "no_checkable_claims"),
        (
            judging(
                claims("Your booking is confirmed."),
                verdicts((0, "not_applicable", [], None)),
            ),
            "no_checkable_claims",
        ),
        (judging(JudgeUnreachable("down")), "failed"),
        (
            judging(claims("The deposit is one month's rent."), JudgeUnreachable("down")),
            "failed",
        ),
        (
            judging(claims("The deposit is one month's rent."), verdicts()),
            "failed",
        ),
    ],
)
def test_score_is_non_null_if_and_only_if_the_status_is_scored(
    make_client, judge, expected_status
):
    _, engine = run_turn(make_client, searching("deposit", "One month's rent."), judge)

    check = check_of(engine)
    assert check.status == expected_status
    assert (check.score is not None) == (check.status == "scored")


def test_the_no_context_path_also_holds_the_invariant(make_client):
    _, engine = run_turn(
        make_client,
        searching("parking", "No confirmed answer."),
        judging(claims("unused")),
        message="parking?",
    )

    check = check_of(engine)
    assert (check.score is not None) == (check.status == "scored")


def test_no_rows_are_written_when_the_check_is_disabled(make_client):
    judge = judging(
        claims("The deposit is one month's rent."),
        verdicts((0, "supported", ["faq-deposit"], None)),
    )
    response, engine = run_turn(
        make_client,
        searching("deposit", "One month's rent."),
        judge,
        faithfulness_check_enabled=False,
    )

    assert rows(engine) == []
    assert rows(engine, FaithfulnessClaim) == []
    # The turn itself is untouched by the switch.
    assert response.json()["reply"] == "One month's rent."
    assert len(rows(engine, Message)) == 2


@pytest.fixture
def persisted_turn(tmp_path):
    """A committed user + conversation + assistant message, for direct-call tests."""
    settings = make_db_settings(tmp_path)
    engine = build_engine(settings)
    create_tables(engine)
    with Session(engine) as session:
        user = User(
            id="user-fcheck",
            name="Andi",
            email="andi@example.test",
            role="client",
            hashed_password="x",
        )
        conversation = Conversation(user_id=user.id)
        session.add(user)
        # One flush per dependency layer: these models declare no relationship(), so
        # SQLAlchemy cannot derive insert order from them and may emit the Conversation
        # before the User it references. Same footgun app/db/seed.py documents.
        session.flush()
        session.add(conversation)
        session.commit()
        message = Message(
            conversation_id=conversation.id, role="assistant", content="One month's rent."
        )
        session.add(message)
        session.commit()
        return settings, engine, user.id, conversation.id, message.id


async def _check(engine, settings, judge, message_id, conversation_id, user_id):
    await run_faithfulness_check(
        engine,
        settings,
        judge,
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        reply_text="The deposit is one month's rent.",
        context=[
            ContextEntry(
                faq_id=DEPOSIT.id, question=DEPOSIT.question, answer=DEPOSIT.answer
            )
        ],
    )


@pytest.mark.anyio
async def test_a_second_check_for_the_same_message_is_rejected_not_overwritten(
    persisted_turn,
):
    """Enforced by the database's unique constraint, not by a read-then-write race."""
    settings, engine, user_id, conversation_id, message_id = persisted_turn

    def fresh_judge():
        return judging(
            claims("The deposit is one month's rent."),
            verdicts((0, "supported", ["faq-deposit"], None)),
        )

    await _check(engine, settings, fresh_judge(), message_id, conversation_id, user_id)
    await _check(engine, settings, fresh_judge(), message_id, conversation_id, user_id)

    persisted = rows(engine)
    assert len(persisted) == 1
    assert persisted[0].status == "scored"
    assert len(rows(engine, FaithfulnessClaim)) == 1


@pytest.mark.anyio
async def test_the_concurrency_gate_bounds_checks_scheduled_from_different_turns(
    persisted_turn,
):
    """One semaphore per event loop, not per call — otherwise a burst of turns would
    spawn unbounded concurrent judge calls."""
    settings = persisted_turn[0]

    gate = faithfulness._concurrency_gate(settings.faithfulness_max_concurrent)
    assert faithfulness._concurrency_gate(settings.faithfulness_max_concurrent) is gate
    assert gate._value == settings.faithfulness_max_concurrent
