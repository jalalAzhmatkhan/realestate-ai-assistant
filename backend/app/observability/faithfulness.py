"""Answer faithfulness: is the assistant's reply actually grounded in what RAG returned?

Specified by ``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md``
decision 4. Two LLM calls — decompose the reply into claims, then judge every claim
against the retrieved context in one batched request — run **after** the chat response
has already been sent, as a FastAPI background task with its own database session.

Two properties are the whole point of this module:

1. **It never touches the request path.** Two extra model round trips would roughly
   triple perceived chat latency for a number the user never sees, so the check is
   scheduled, not awaited, and any exception inside it dies here rather than escaping
   into the turn that scheduled it.
2. **Every failure degrades to a missing datum, never a fabricated number.** A timed-out
   judge, a malformed judgment, or a verdict count that does not match the claim count
   fails the *whole* check with ``score=null`` — a partial score would be a made-up
   score, and a made-up faithfulness score is worse than none at all. The resulting
   invariant, which the admin surface and QA both rely on: ``score`` is non-null **if and
   only if** ``status == "scored"``. ``0.0`` is a real score (every checkable claim was
   unsupported); ``null`` means not computed.

Scope is deliberately narrow: only turns that called ``search_faq``. A turn that never
consulted the knowledge base gets no row at all — not a null score, not a zero — because
faithfulness-to-retrieved-context is undefined when nothing was retrieved.
"""

import asyncio
import logging
import time
import weakref
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.agent.deps import RetrievalRecord
from app.core.config import Settings
from app.models import (
    FAITHFULNESS_RATIONALE_MAX_LENGTH,
    FaithfulnessCheck,
    FaithfulnessClaim,
    FaithfulnessStatus,
)
from app.rag.index import FaqRetriever

logger = logging.getLogger(__name__)

DECOMPOSITION_INSTRUCTIONS = """\
You split an assistant's reply to a customer into the individual factual claims it makes.

A claim is one self-contained, verifiable statement, rewritten to stand on its own (resolve
pronouns and references, so "it costs one month's rent" becomes "The deposit costs one
month's rent"). Split compound sentences into separate claims.

Return an empty list when the reply asserts no facts at all — a greeting, an
acknowledgement, a clarifying question, or an offer to connect the user with a human.
Do not include questions, offers, pleasantries, or hedges as claims.
Do not invent claims the reply does not make, and do not judge whether any claim is true.\
"""

JUDGE_INSTRUCTIONS = """\
You check whether each numbered claim is supported by the provided knowledge base entries,
and nothing else. Your own knowledge of the world is irrelevant here.

Return exactly one judgment per claim, each carrying the claim's own index. Never merge,
skip, reorder-without-index, or add claims.

Verdicts:
- "supported": the entries state or directly entail the claim. List the ids of the entries
  that support it.
- "unsupported": the entries do not state or entail the claim — including a claim that
  goes beyond what they say, contradicts them, or adds a detail they do not mention.
- "not_applicable": the claim is not about company policy, process, or terms at all, so
  the knowledge base is not the right source for it (for example a booking confirmation
  detail, a specific property listing, a reference number, or the current date).

Keep each rationale to one short sentence.\
"""


class ClaimDecomposition(BaseModel):
    """Structured output of the first call."""

    claims: list[str] = Field(
        default_factory=list,
        description="Each factual claim the reply makes, rewritten to stand alone.",
    )


class ClaimVerdict(BaseModel):
    """One judged claim. ``claim_index`` is what makes a mismatched batch detectable."""

    claim_index: int = Field(description="The 0-based index of the claim being judged.")
    verdict: Literal["supported", "unsupported", "not_applicable"]
    supporting_faq_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the entries supporting the claim; empty unless supported.",
    )
    rationale: str | None = Field(
        default=None, description="One short sentence explaining the verdict."
    )


class ClaimJudgment(BaseModel):
    """Structured output of the second call: every claim judged in one request."""

    judgments: list[ClaimVerdict] = Field(default_factory=list)


@dataclass(frozen=True)
class ContextEntry:
    """One knowledge base entry that reached the model this turn."""

    faq_id: str
    question: str
    answer: str


@dataclass(frozen=True)
class _Outcome:
    """The verdict on the check itself, before it becomes rows."""

    status: FaithfulnessStatus
    score: float | None = None
    error_code: str | None = None
    claims: list[tuple[str, ClaimVerdict]] = field(default_factory=list)
    counts: dict[str, int] | None = None


def collect_context(rag: FaqRetriever, records: Iterable[RetrievalRecord]) -> list[ContextEntry]:
    """The deduplicated union of everything retrieved this turn, in first-seen order.

    A turn may call ``search_faq`` more than once; the reply is grounded in the union of
    what those calls returned, so judging against one call's results alone would mark
    correctly-grounded claims unsupported. Entry text is resolved from the index because
    the retrieval record carries only what identifies a hit, not its answer body.
    """
    context: dict[str, ContextEntry] = {}
    for record in records:
        for result in record.results:
            faq_id = result.get("faq_id")
            if not faq_id or faq_id in context:
                continue
            entry = rag.get(faq_id)
            if entry is None:
                continue
            context[faq_id] = ContextEntry(
                faq_id=faq_id, question=entry.question, answer=entry.answer
            )
    return list(context.values())


async def decompose_claims(model: Model, reply_text: str) -> list[str]:
    """First LLM call: the reply, as a list of standalone claims."""
    agent = Agent[None, ClaimDecomposition](
        model,
        output_type=ClaimDecomposition,
        instructions=DECOMPOSITION_INSTRUCTIONS,
        name="faithfulness-decomposer",
    )
    result = await agent.run(f"Assistant reply:\n{reply_text}")
    return [claim.strip() for claim in result.output.claims if claim.strip()]


async def judge_claims(
    model: Model, claims: list[str], context: list[ContextEntry]
) -> list[ClaimVerdict]:
    """Second LLM call: every claim judged against the context, in one request.

    Batched rather than one request per claim — N requests would multiply cost and
    latency for no extra signal, and would make a partially-failed check possible, which
    the "never a fabricated number" rule forbids anyway.
    """
    agent = Agent[None, ClaimJudgment](
        model,
        output_type=ClaimJudgment,
        instructions=JUDGE_INSTRUCTIONS,
        name="faithfulness-judge",
    )
    rendered_context = "\n\n".join(
        f"[{entry.faq_id}] Q: {entry.question}\nA: {entry.answer}" for entry in context
    )
    rendered_claims = "\n".join(f"{index}. {claim}" for index, claim in enumerate(claims))
    result = await agent.run(
        f"Knowledge base entries:\n{rendered_context}\n\n"
        f"Claims to judge ({len(claims)} total, indices 0-{len(claims) - 1}):\n"
        f"{rendered_claims}"
    )
    return result.output.judgments


def _align(claims: list[str], verdicts: list[ClaimVerdict]) -> list[ClaimVerdict] | None:
    """Verdicts in claim order, or ``None`` if the batch does not cover the claims exactly.

    A judge that returned the wrong number of verdicts, a duplicate index, or an index
    out of range has produced something we cannot attribute — scoring the subset it did
    return would publish a number computed over a claim set nobody chose.
    """
    if len(verdicts) != len(claims):
        return None
    by_index = {verdict.claim_index: verdict for verdict in verdicts}
    if set(by_index) != set(range(len(claims))):
        return None
    return [by_index[index] for index in range(len(claims))]


async def _evaluate(
    model: Model, settings: Settings, reply_text: str, context: list[ContextEntry]
) -> _Outcome:
    if not context:
        # `search_faq` ran and found nothing. The correct reply to that is "I have no
        # confirmed answer" — scoring it 0.0 would record the agent's honesty as a
        # hallucination, which is exactly backwards.
        return _Outcome(status="no_context")

    try:
        claims = await decompose_claims(model, reply_text)
    except Exception:
        logger.exception("faithfulness_decomposition_failed")
        return _Outcome(status="failed", error_code="decomposition_failed")

    if len(claims) > settings.faithfulness_max_claims:
        # Runaway decomposition means the reply was mis-parsed, not that it made 40
        # claims. Judging it anyway would spend a large batched call on noise.
        logger.warning(
            "faithfulness_claim_limit_exceeded",
            extra={"claim_count": len(claims), "limit": settings.faithfulness_max_claims},
        )
        return _Outcome(status="failed", error_code="decomposition_failed")

    if not claims:
        return _Outcome(status="no_checkable_claims", counts=_zero_counts())

    try:
        verdicts = await judge_claims(model, claims, context)
    except Exception:
        logger.exception("faithfulness_judging_failed")
        return _Outcome(status="failed", error_code="judging_failed")

    aligned = _align(claims, verdicts)
    if aligned is None:
        logger.warning(
            "faithfulness_judgment_malformed",
            extra={"claim_count": len(claims), "verdict_count": len(verdicts)},
        )
        return _Outcome(status="failed", error_code="malformed_judgment")

    judged = list(zip(claims, aligned))
    counts = {
        "supported": sum(1 for _, v in judged if v.verdict == "supported"),
        "unsupported": sum(1 for _, v in judged if v.verdict == "unsupported"),
        "not_applicable": sum(1 for _, v in judged if v.verdict == "not_applicable"),
    }
    checkable = counts["supported"] + counts["unsupported"]
    if checkable == 0:
        # Every claim came from somewhere other than the FAQ corpus (a booking
        # confirmation, a listing detail). There is nothing here to be faithful *to*.
        return _Outcome(status="no_checkable_claims", claims=judged, counts=counts)

    return _Outcome(
        status="scored",
        score=counts["supported"] / checkable,
        claims=judged,
        counts=counts,
    )


def _zero_counts() -> dict[str, int]:
    return {"supported": 0, "unsupported": 0, "not_applicable": 0}


def _persist(
    engine: Engine,
    outcome: _Outcome,
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    context: list[ContextEntry],
    judge_model: str,
    duration_ms: int,
) -> None:
    context_ids = [entry.faq_id for entry in context]
    known_ids = set(context_ids)
    counts = outcome.counts
    check = FaithfulnessCheck(
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        status=outcome.status,
        score=outcome.score,
        supported_count=counts["supported"] if counts else None,
        unsupported_count=counts["unsupported"] if counts else None,
        not_applicable_count=counts["not_applicable"] if counts else None,
        claim_count=sum(counts.values()) if counts else None,
        context_faq_ids=context_ids,
        judge_model=judge_model,
        error_code=outcome.error_code,
        duration_ms=duration_ms,
    )
    with Session(engine) as session:
        session.add(check)
        for index, (claim_text, verdict) in enumerate(outcome.claims):
            session.add(
                FaithfulnessClaim(
                    check_id=check.id,
                    claim_index=index,
                    claim_text=claim_text,
                    verdict=verdict.verdict,
                    # Citations are only meaningful on a supported verdict, and only for
                    # entries that were actually in context — a judge naming an id it was
                    # never shown has hallucinated it.
                    supporting_faq_ids=(
                        [i for i in verdict.supporting_faq_ids if i in known_ids]
                        if verdict.verdict == "supported"
                        else []
                    ),
                    rationale=_truncate(verdict.rationale),
                )
            )
        session.commit()


def _truncate(rationale: str | None) -> str | None:
    if rationale is None:
        return None
    if len(rationale) <= FAITHFULNESS_RATIONALE_MAX_LENGTH:
        return rationale
    return rationale[: FAITHFULNESS_RATIONALE_MAX_LENGTH - 3] + "..."


# One semaphore per (event loop, limit). Background checks are scheduled from independent
# requests, so the bound has to live outside any single one of them; keying by loop keeps
# a semaphore from being awaited on a loop other than the one it was created under, which
# a process serving several app instances (every test run) would otherwise hit.
_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)


def _concurrency_gate(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    cached = _SEMAPHORES.get(loop)
    if cached is None or cached[0] != limit:
        cached = (limit, asyncio.Semaphore(limit))
        _SEMAPHORES[loop] = cached
    return cached[1]


async def run_faithfulness_check(
    engine: Engine,
    settings: Settings,
    judge_model: Model,
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    reply_text: str,
    context: list[ContextEntry],
) -> None:
    """Score one assistant turn and persist the result. Never raises.

    Runs after the response is sent, on its own session — the request-scoped one is
    closed by then. This function is the background task's entire boundary, so anything
    that goes wrong inside it becomes a ``failed`` row or, if even the write fails, a log
    line. There are no retries: a missing faithfulness datum costs an admin one blank
    cell, and a retry would double the cost of exactly the calls that just proved
    expensive.
    """
    started = time.perf_counter()
    outcome = _Outcome(status="failed", error_code="internal_error")
    model_name = "unknown"
    try:
        model_name = getattr(judge_model, "model_name", "unknown")
        async with _concurrency_gate(settings.faithfulness_max_concurrent):
            outcome = await _evaluate(judge_model, settings, reply_text, context)
    except Exception:
        logger.exception(
            "faithfulness_check_internal_error",
            extra={"message_id": message_id, "conversation_id": conversation_id},
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        _persist(
            engine,
            outcome,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            context=context,
            judge_model=model_name,
            duration_ms=duration_ms,
        )
    except IntegrityError:
        # The unique constraint on message_id did its job. A duplicate check is a
        # scheduling accident, not a reason to overwrite the first verdict.
        logger.warning(
            "faithfulness_check_duplicate", extra={"message_id": message_id}
        )
        return
    except Exception:
        logger.exception(
            "faithfulness_check_not_persisted", extra={"message_id": message_id}
        )
        return

    logger.info(
        "faithfulness_check_completed",
        extra={
            "message_id": message_id,
            "conversation_id": conversation_id,
            "status": outcome.status,
            "score": outcome.score,
            "error_code": outcome.error_code,
            "context_size": len(context),
            "duration_ms": duration_ms,
        },
    )
