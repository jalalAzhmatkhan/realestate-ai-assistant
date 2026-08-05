"""``escalate_to_human``, exposed to the LLM as ``EscalateToHuman`` — the safety valve.

**Unassigned path only (task B18a).** ``assigned_agent_id`` is always ``None`` and
``status`` always ``"queued_unassigned"``. Listing-agent assignment is task B18b in Phase
4; the shared scoped property query it needs (``app/property/queries.py``) already exists,
so that lands as an additive change to ``_persist_escalation`` rather than a rework.

This tool is what every other tool's failure path points at, so it must not acquire new
ways to break. Persistence failure retries once and then degrades to a static support
reply rather than raising: a user who has already given up on the AI must never be left
with nothing.
"""

import logging
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from sqlalchemy import func
from sqlmodel import col, select

from app.agent.deps import AgentDeps
from app.models import Escalation
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


def _resolve_property_id(ctx: RunContext[AgentDeps], property_id: str | None) -> str | None:
    """Only a *resolved* property id is persisted (assignment contract, Decision 6).

    Resolved through the caller's scoped property query rather than a primary-key read:
    the id is LLM-supplied, and a bare lookup would let a client probe for the existence
    of a draft or sold listing. An unresolvable id is stored as ``None`` and logged, so
    "escalations for property X" queries stay correct.
    """
    if not property_id:
        return None
    prop = find_visible_property(ctx.deps.db, ctx.deps.user, property_id)
    if prop is None:
        logger.warning(
            "escalation_property_unresolved",
            extra={
                "tool": TOOL_NAME,
                "user_id": ctx.deps.user.id,
                "conversation_id": ctx.deps.conversation_id,
                "supplied_property_id": property_id,
            },
        )
        return None
    return prop.id


def _persist_escalation(
    ctx: RunContext[AgentDeps],
    *,
    reason: str,
    category: str,
    conversation_summary: str,
    urgency: str,
    resolved_property_id: str | None,
) -> Escalation:
    escalation = Escalation(
        conversation_id=ctx.deps.conversation_id,
        escalated_by_id=ctx.deps.user.id,
        reason=reason,
        category=category,
        conversation_summary=conversation_summary,
        urgency=urgency,
        property_id=resolved_property_id,
        # B18b (Phase 4) is what makes these two anything else.
        assigned_agent_id=None,
        status="queued_unassigned",
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

    resolved_property_id = _resolve_property_id(ctx, property_id)

    escalation: Escalation | None = None
    for attempt in (1, 2):
        try:
            escalation = _persist_escalation(
                ctx,
                reason=reason,
                category=category,
                conversation_summary=conversation_summary,
                urgency=urgency,
                resolved_property_id=resolved_property_id,
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
        },
    )
    return EscalateToHumanOutput(
        escalation_id=escalation.id,
        status=escalation.status,
        assigned_agent_id=escalation.assigned_agent_id,
        # No promise of a callback: this is an assignment field, not a paging system,
        # and overclaiming here is worse than the no-op it replaces.
        message=(
            f"This has been logged for a human colleague. Reference: {escalation.id}. "
            f"Give the user this reference. Do not tell them anyone will respond, review "
            f"it, or get back to them — nobody was paged."
        ),
    )
