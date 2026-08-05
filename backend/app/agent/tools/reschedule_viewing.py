"""``reschedule_viewing``, exposed to the LLM as ``RescheduleViewing``.

Contract: ``Documentation/audits/2026-08-05-reschedule-viewing-tool-contract.md``.

The slot swap itself is ``app/booking/slots.py::reschedule_booking`` — the same
transaction ``POST /api/v1/bookings/{id}/reschedule`` runs. What this module adds is
**booking resolution**, and that resolution is the authorization boundary: it only ever
searches within ``ctx.deps.user``'s scope, so "exists but not yours" and "does not exist"
are the same answer by construction rather than by a later check someone could forget.
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel
from pydantic_ai import RunContext
from sqlmodel import col, select

from app.agent.deps import AgentDeps
from app.agent.tools.booking_common import as_tool_error
from app.agent.tools.errors import NotFoundToolError, UpstreamToolError, ValidationToolError
from app.booking.queries import scoped_booking_query
from app.booking.slots import BookingDomainError, reschedule_booking
from app.models import Booking, Property
from app.models.types import utcnow
from app.notifications.port import BookingRescheduledEvent

logger = logging.getLogger(__name__)

TOOL_NAME = "RescheduleViewing"
MAX_CANDIDATES = 5
MAX_REASON_LENGTH = 500

TOOL_DESCRIPTION = """\
Move an EXISTING viewing appointment to a different time, keeping the same booking.

Use this whenever the user wants a viewing they already have to happen at a different
time: "actually, can we make it Monday instead?", "move my Saturday viewing to the
afternoon", "something came up, can we push it back an hour?". The giveaway is that they
are talking about an appointment that already exists, not asking for an additional one —
if they want to see another property, or the same property a second time, that is
BookViewing instead. Getting this wrong creates a duplicate appointment and leaves the
original one standing, so when it is genuinely unclear, ask.

This can only change WHEN a viewing happens. It cannot change which property is being
viewed or which agent conducts it.

You do NOT need a booking id. The user almost certainly does not know one, and the server
finds the right booking within what this user is allowed to see. Supply whichever of
`booking_id`, `property_id`, and `current_slot_time` the conversation has actually
established, and nothing more — inventing an identifier makes the lookup fail.

If this returns `booking_ambiguous`, several of the user's viewings matched, and you must
ASK THE USER which one they mean. Use the `candidates` list to describe the options by
property and time, then wait for their answer before calling this tool again. Do NOT pick
a candidate yourself and do NOT re-call this tool with a candidate the user has not
chosen — the other candidates are real appointments, and moving the wrong one disrupts a
real person's day.

If this returns `booking_not_found`, say you could not find a matching upcoming viewing
and offer to book one. Do not speculate about whether some particular booking exists.\
"""


class RescheduleViewingOutput(BaseModel):
    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    previous_slot_time: datetime
    status: str
    rescheduled_count: int


def _candidate_payload(ctx: RunContext[AgentDeps], bookings: list[Booking]) -> list[dict]:
    """``{booking_id, property_title, slot_time}`` and nothing else.

    Field-limited on purpose (contract decision #9): a ``client`` disambiguating their
    own viewings must not receive other people's names or contact details, and an
    ``agent`` should not get more than the question requires.
    """
    titles = {
        prop.id: prop.title
        for prop in ctx.deps.db.exec(
            select(Property).where(
                col(Property.id).in_([booking.property_id for booking in bookings])
            )
        ).all()
    }
    return [
        {
            "booking_id": booking.id,
            "property_title": titles.get(booking.property_id, "Unknown listing"),
            "slot_time": booking.slot_time.astimezone(timezone.utc).isoformat(),
        }
        for booking in bookings
    ]


def _resolve_booking(
    ctx: RunContext[AgentDeps],
    *,
    booking_id: str | None,
    property_id: str | None,
    current_slot_time: datetime | None,
) -> Booking:
    """Find the one booking to move, within the caller's scope only.

    Resolution order is the contract's table: an explicit ``booking_id`` is looked up
    directly; otherwise the caller's ``confirmed``, future bookings are filtered by
    whichever hints were supplied, or returned outright if none were.
    """
    db = ctx.deps.db
    scoped = scoped_booking_query(ctx.deps.user)

    if booking_id:
        booking = db.exec(scoped.where(col(Booking.id) == booking_id)).first()
        if booking is None:
            # Same error as "no such booking" — see this module's docstring.
            raise NotFoundToolError(
                "No matching viewing was found.",
                code="booking_not_found",
                guidance=(
                    "Tell the user you could not find a matching upcoming viewing, and "
                    "offer to book one. Do not confirm or deny that any particular "
                    "booking exists."
                ),
            )
        return booking

    statement = scoped.where(
        col(Booking.status) == "confirmed", col(Booking.slot_time) > utcnow()
    )
    if property_id:
        statement = statement.where(col(Booking.property_id) == property_id)
    if current_slot_time:
        statement = statement.where(col(Booking.slot_time) == current_slot_time)

    matches = list(
        db.exec(statement.order_by(col(Booking.slot_time)).limit(MAX_CANDIDATES + 1)).all()
    )

    if not matches:
        raise NotFoundToolError(
            "No matching upcoming viewing was found.",
            code="booking_not_found",
            guidance=(
                "Tell the user you could not find a matching upcoming viewing, and offer "
                "to book one. Do not confirm or deny that any particular booking exists."
            ),
        )
    if len(matches) > 1:
        # The five soonest. admin/agent callers have wide scope and will land here often;
        # that is intended — they are expected to supply a booking_id.
        raise ValidationToolError(
            "Several upcoming viewings match that description.",
            code="booking_ambiguous",
            guidance=(
                "STOP. Ask the user which of these viewings they mean, describing them by "
                "property and time. Never choose one yourself, and never call this tool "
                "again with a booking_id the user has not explicitly confirmed."
            ),
            candidates=_candidate_payload(ctx, matches[:MAX_CANDIDATES]),
        )
    return matches[0]


async def reschedule_viewing(
    ctx: RunContext[AgentDeps],
    requested_slot_time: datetime,
    booking_id: str | None = None,
    property_id: str | None = None,
    current_slot_time: datetime | None = None,
    reason: str | None = None,
) -> RescheduleViewingOutput:
    """Move an existing viewing to a different time.

    Args:
        requested_slot_time: The new appointment time, ISO-8601 with an explicit UTC
            offset (e.g. "2026-08-08T13:00:00+07:00").
        booking_id: The booking to move, only if the conversation already surfaced a
            real id. Leave empty otherwise — do not invent one.
        property_id: Which listing's viewing to move, if the user identified it by
            property rather than by id.
        current_slot_time: The viewing's existing time, if the user identified it that
            way ("my Saturday 10am viewing"). ISO-8601 with a UTC offset.
        reason: Optional short note on why it is being moved, in the user's own terms.
    """
    if requested_slot_time.tzinfo is None:
        raise ValidationToolError(
            "requested_slot_time must include an explicit UTC offset.",
            code="slot_time_not_timezone_aware",
            guidance=(
                "Re-send the time with an offset, e.g. 2026-08-08T13:00:00+07:00. Do not "
                "guess the offset if the user's timezone is genuinely unclear — ask."
            ),
        )
    if reason is not None and len(reason) > MAX_REASON_LENGTH:
        reason = reason[:MAX_REASON_LENGTH]

    user = ctx.deps.user
    booking = _resolve_booking(
        ctx,
        booking_id=booking_id,
        property_id=property_id,
        current_slot_time=current_slot_time,
    )

    try:
        result = reschedule_booking(
            ctx.deps.db, booking, requested_slot_time=requested_slot_time
        )
    except BookingDomainError as exc:
        logger.info(
            "tool_call_rejected",
            extra={
                "tool": TOOL_NAME,
                "user_id": user.id,
                "role": user.role,
                "conversation_id": ctx.deps.conversation_id,
                "booking_id": booking.id,
                "error_code": exc.code,
            },
        )
        raise as_tool_error(exc) from None
    except Exception:
        logger.exception(
            "reschedule_viewing_failed",
            extra={"tool": TOOL_NAME, "user_id": user.id, "booking_id": booking.id},
        )
        raise UpstreamToolError(
            "The viewing could not be moved.",
            guidance=(
                "Apologize, confirm the original viewing is unchanged, and offer to "
                "connect the user with a human agent."
            ),
        ) from None

    moved = result.booking
    # Identical payload to the REST path's, so a downstream consumer cannot tell — and
    # does not need to care — whether the reschedule came from chat or the dashboard.
    ctx.deps.notifier.publish_booking_rescheduled(
        BookingRescheduledEvent(
            booking_id=moved.id,
            property_id=moved.property_id,
            client_id=moved.client_id,
            agent_id=moved.agent_id,
            slot_time=moved.slot_time,
            previous_slot_time=result.previous_slot_time,
            rescheduled_count=moved.rescheduled_count,
            status=moved.status,
            updated_at=moved.updated_at,
            reason=reason,
        )
    )

    logger.info(
        "tool_call",
        extra={
            "tool": TOOL_NAME,
            "user_id": user.id,
            "role": user.role,
            "conversation_id": ctx.deps.conversation_id,
            "booking_id": moved.id,
            "rescheduled_count": moved.rescheduled_count,
        },
    )
    return RescheduleViewingOutput(
        booking_id=moved.id,
        property_id=moved.property_id,
        client_id=moved.client_id,
        agent_id=moved.agent_id,
        slot_time=moved.slot_time.astimezone(timezone.utc),
        previous_slot_time=result.previous_slot_time.astimezone(timezone.utc),
        status=moved.status,
        rescheduled_count=moved.rescheduled_count,
    )
