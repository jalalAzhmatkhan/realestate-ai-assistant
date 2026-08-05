"""``escalate_to_human``, exposed to the LLM as ``EscalateToHuman`` — the safety valve.

Listing-agent assignment (task B18b) follows
``Documentation/audits/2026-08-05-escalation-assignment-contract.md`` Section 1: when the
escalation carries a ``property_id`` that resolves *within the caller's RBAC scope* to a
listing whose agent still exists, is ``active``, and holds an ``agent``/``admin`` role,
that agent becomes ``assigned_agent_id`` and the status is ``"queued"``. Every other
branch is ``queued_unassigned``.

**This is an assignment field, not a handoff.** Nobody is paged, so the ``message`` must
not imply a reply is coming (Decision 7).

This tool is what every other tool's failure path points at, so it must not acquire new
ways to break. Assignment resolution therefore never fails the call — including an
exception from the lookup itself — and persistence failure retries once and then degrades
to a static support reply rather than raising: a user who has already given up on the AI
must never be left with nothing.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from sqlalchemy import func
from sqlmodel import col, select

from app.agent.deps import AgentDeps
from app.models import Escalation, EscalationStatus, User
from app.models.types import utcnow
from app.notifications.port import EscalationCreatedEvent
from app.property.queries import find_visible_property

logger = logging.getLogger(__name__)

TOOL_NAME = "EscalateToHuman"
SUPPORT_CONTACT = "support@evdekimi.test"
# Deliberately generous: this is abuse control, not a quota. Three logged escalations in
# an hour on one conversation is already well past "the user needs a human".
MAX_ESCALATIONS_PER_CONVERSATION = 3
RATE_LIMIT_WINDOW = timedelta(hours=1)

TOOL_DESCRIPTION = """\
Hand this conversation to a human colleague and log it for follow-up.

Call this when:
- the user asks for a human, a manager, or "someone real", however they phrase it;
- they are complaining, upset, or repeating themselves because you have not helped;
- the request needs a decision you cannot make — a discount, a policy exception, a
  contract or legal question, anything about someone else's account;
- the FAQ search came back with nothing and you would otherwise be guessing at policy;
- a tool has failed more than once and you have no other way forward;
- the topic is outside real estate entirely.

Prefer escalating over improvising. A wrong answer about a deposit, a lease term, or a
legal requirement costs the user real money; "let me get a person on this" costs them a
short wait.

Write `reason` and `conversation_summary` for the colleague who picks this up, not for the
user: what they asked, what you already tried, and what is still unresolved. Set `urgency`
from actual impact, not from tone.

After calling this, tell the user their request has been logged and give them the
reference — and stop there. Do NOT say anyone will call, email, review it, get back to
them, or respond, in any wording. This creates a record; it does not page a human. You
have no way to know a reply is coming, and the user will wait on it if you imply one.\
"""


class EscalateToHumanOutput(BaseModel):
    # `str | None` rather than the frozen contract's `str`: null marks the degraded path
    # where persistence failed twice and there is genuinely no reference to give. Every
    # successful call still returns an id. Flagged as a deviation in the phase report.
    escalation_id: str | None
    status: Literal["queued", "queued_unassigned"]
    assigned_agent_id: str | None
    message: str


def _recent_escalation_count(ctx: RunContext[AgentDeps]) -> int:
    """Rate limiting by counting persisted rows, not an in-process counter.

    A per-process dict would be wrong the moment there is more than one instance and
    would contradict the stateless-services principle for a control this cheap to derive
    from data we already store.
    """
    since = utcnow() - RATE_LIMIT_WINDOW
    return int(
        ctx.deps.db.exec(
            select(func.count())
            .select_from(Escalation)
            .where(
                col(Escalation.conversation_id) == ctx.deps.conversation_id,
                col(Escalation.created_at) >= since,
            )
        ).one()
    )


def _latest_escalation(ctx: RunContext[AgentDeps]) -> Escalation | None:
    return ctx.deps.db.exec(
        select(Escalation)
        .where(col(Escalation.conversation_id) == ctx.deps.conversation_id)
        .order_by(col(Escalation.created_at).desc())
    ).first()


@dataclass(frozen=True)
class _Assignment:
    """What the assignment lookup concluded. ``UNASSIGNED`` is the value every negative
    branch (E1-E6) degrades to, so no caller has to remember the pairing."""

    property_id: str | None
    agent_id: str | None
    status: EscalationStatus


UNASSIGNED = _Assignment(property_id=None, agent_id=None, status="queued_unassigned")


def _resolve_assignment(ctx: RunContext[AgentDeps], property_id: str | None) -> _Assignment:
    """Resolve the listing and its agent, per the contract's E1-E8 table.

    The property is loaded through the caller's **scoped** query, never by primary key:
    the id is LLM-supplied, so a bare read would let a client probe for the existence and
    owner of a ``draft``/``sold`` listing by watching whether an assignee came back
    (Decision 2). "Does not exist" (E2) and "not visible to you" (E3) are therefore one
    answer, indistinguishable in every observable.

    Only a *resolved* property id is returned for persistence (Decision 6), so
    "escalations for property X" queries stay correct.
    """
    if not property_id:
        return UNASSIGNED  # E1

    prop = find_visible_property(ctx.deps.db, ctx.deps.user, property_id)
    if prop is None:
        # Verbose logging is fine here; a distinguishable *response* would not be.
        logger.warning(
            "escalation_property_unresolved",
            extra={
                "tool": TOOL_NAME,
                "user_id": ctx.deps.user.id,
                "conversation_id": ctx.deps.conversation_id,
                "supplied_property_id": property_id,
            },
        )
        return UNASSIGNED  # E2, E3

    if prop.agent_id is None:
        return _Assignment(prop.id, None, "queued_unassigned")  # E4

    agent = ctx.deps.db.get(User, prop.agent_id)
    if agent is None or agent.status != "active" or agent.role not in ("agent", "admin"):
        # Naming an account that cannot act is worse than naming nobody: the record
        # would claim an assignee while being addressed to no one.
        logger.info(
            "escalation_assignee_unavailable",
            extra={
                "tool": TOOL_NAME,
                "property_id": prop.id,
                "listing_agent_id": prop.agent_id,
            },
        )
        return _Assignment(prop.id, None, "queued_unassigned")  # E5, E6

    # Self-assignment is deliberate (Decision 4): the record means "this concerns listing
    # L, whose owner is agent A", which is true regardless of who typed the message.
    return _Assignment(prop.id, agent.id, "queued")  # E7, E8


def _resolve_assignment_safely(
    ctx: RunContext[AgentDeps], property_id: str | None
) -> _Assignment:
    """Assignment must never become a new way for the safety valve to fail — even a DB
    error degrades to the unassigned path and the escalation still gets logged."""
    try:
        return _resolve_assignment(ctx, property_id)
    except Exception:
        logger.exception(
            "escalation_assignment_failed",
            extra={
                "tool": TOOL_NAME,
                "user_id": ctx.deps.user.id,
                "conversation_id": ctx.deps.conversation_id,
            },
        )
        return UNASSIGNED


def _persist_escalation(
    ctx: RunContext[AgentDeps],
    *,
    reason: str,
    category: str,
    conversation_summary: str,
    urgency: str,
    assignment: _Assignment,
) -> Escalation:
    escalation = Escalation(
        conversation_id=ctx.deps.conversation_id,
        escalated_by_id=ctx.deps.user.id,
        reason=reason,
        category=category,
        conversation_summary=conversation_summary,
        urgency=urgency,
        property_id=assignment.property_id,
        assigned_agent_id=assignment.agent_id,
        status=assignment.status,
    )
    ctx.deps.db.add(escalation)
    ctx.deps.db.commit()
    ctx.deps.db.refresh(escalation)
    return escalation


async def escalate_to_human(
    ctx: RunContext[AgentDeps],
    reason: str,
    conversation_summary: str,
    category: Literal[
        "complaint", "complex_request", "policy_exception", "technical_issue", "other"
    ] = "other",
    urgency: Literal["low", "medium", "high"] = "medium",
    property_id: str | None = None,
) -> EscalateToHumanOutput:
    """Log this conversation for a human colleague to pick up.

    Args:
        reason: Why a human is needed, in one or two sentences, written for the
            colleague who will read it.
        conversation_summary: What the user asked, what you already tried, and what is
            still unresolved.
        category: Which kind of request this is.
        urgency: How much actual impact a delay has for the user.
        property_id: The listing this is about, if there is one.
    """
    user = ctx.deps.user

    if _recent_escalation_count(ctx) >= MAX_ESCALATIONS_PER_CONVERSATION:
        # Not an error and not a refusal: the user still gets a reference. Only the row
        # spam is stopped, so the safety valve itself stays available.
        existing = _latest_escalation(ctx)
        logger.warning(
            "escalation_rate_limited",
            extra={
                "tool": TOOL_NAME,
                "user_id": user.id,
                "conversation_id": ctx.deps.conversation_id,
            },
        )
        return EscalateToHumanOutput(
            escalation_id=existing.id if existing else None,
            status="queued_unassigned",
            assigned_agent_id=None,
            message=(
                "This conversation has already been logged for a human colleague"
                + (f". Reference: {existing.id}." if existing else ".")
                + " Tell the user it is already with the team and do not log it again."
            ),
        )

    assignment = _resolve_assignment_safely(ctx, property_id)

    escalation: Escalation | None = None
    for attempt in (1, 2):
        try:
            escalation = _persist_escalation(
                ctx,
                reason=reason,
                category=category,
                conversation_summary=conversation_summary,
                urgency=urgency,
                assignment=assignment,
            )
            break
        except Exception:
            ctx.deps.db.rollback()
            logger.exception(
                "escalation_persist_failed",
                extra={
                    "tool": TOOL_NAME,
                    "user_id": user.id,
                    "conversation_id": ctx.deps.conversation_id,
                    "attempt": attempt,
                },
            )

    if escalation is None:
        # Both attempts failed. Still a successful tool result carrying a usable next
        # step — raising here would leave a user who already asked for a human with an
        # apology and nothing else.
        return EscalateToHumanOutput(
            escalation_id=None,
            status="queued_unassigned",
            assigned_agent_id=None,
            message=(
                f"This could not be logged automatically. Ask the user to contact "
                f"{SUPPORT_CONTACT} directly, and apologize for the extra step."
            ),
        )

    ctx.deps.notifier.publish_escalation_created(
        EscalationCreatedEvent(
            escalation_id=escalation.id,
            conversation_id=escalation.conversation_id,
            escalated_by_id=escalation.escalated_by_id,
            escalated_by_role=user.role,
            category=escalation.category,
            urgency=escalation.urgency,
            property_id=escalation.property_id,
            assigned_agent_id=escalation.assigned_agent_id,
            status=escalation.status,
            created_at=escalation.created_at,
        )
    )

    logger.info(
        "tool_call",
        extra={
            "tool": TOOL_NAME,
            "user_id": user.id,
            "role": user.role,
            "conversation_id": ctx.deps.conversation_id,
            "escalation_id": escalation.id,
            "category": escalation.category,
            "urgency": escalation.urgency,
            "status": escalation.status,
            "assigned_agent_id": escalation.assigned_agent_id,
        },
    )
    return EscalateToHumanOutput(
        escalation_id=escalation.id,
        status=escalation.status,
        assigned_agent_id=escalation.assigned_agent_id,
        message=_result_message(escalation),
    )


def _result_message(escalation: Escalation) -> str:
    """Decision 7's two reference sentences, each followed by the standing instruction
    that keeps the model from turning either into a callback promise.

    Assignment records who a listing belongs to; it pages nobody, so "flagged it to the
    agent handling that listing" is the strongest true claim available.
    """
    logged = (
        "I've logged this and flagged it to the agent handling that listing."
        if escalation.status == "queued"
        else "I've logged this for our support team."
    )
    return (
        f"{logged} Reference: {escalation.id}. Give the user this reference. Do not tell "
        f"them anyone will respond, review it, or get back to them — nobody was paged."
    )
