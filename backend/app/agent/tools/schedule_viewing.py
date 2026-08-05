"""``schedule_viewing``, exposed to the LLM as ``BookViewing`` — claim a viewing slot.

Conflict detection is **not** here: it is ``app/booking/slots.py::create_booking``, the
one implementation all three booking write paths share (README Design Decisions §9).
What this module adds is RBAC re-authorization and error rendering.
"""

import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from sqlmodel import col

from app.agent.deps import AgentDeps
from app.agent.tools.booking_common import as_tool_error
from app.agent.tools.errors import UpstreamToolError, ValidationToolError
from app.booking.slots import BookingDomainError, create_booking
from app.models import Property, User
from app.notifications.port import BookingCreatedEvent
from app.property.queries import scoped_property_query

logger = logging.getLogger(__name__)

TOOL_NAME = "BookViewing"

TOOL_DESCRIPTION = """\
Book a NEW property viewing appointment at a specific date and time.

Use this when the user wants to see a property they do not already have a viewing for.
"Can I see the Kemang apartment on Saturday?" and "I'd also like to visit the other unit
on Monday" are both new bookings — the second one adds an appointment rather than
replacing one.

Do NOT use this to change the time of a viewing the user already has. "Actually, can we
make it Monday instead?", "move my Saturday viewing to the afternoon", and "can we push
it back an hour?" are all requests to modify an existing appointment — use
RescheduleViewing for those. Booking a new viewing when the user meant to move an
existing one leaves them with two appointments and nobody told about the first, so when
it is genuinely unclear whether they want an additional viewing or a different time for
the one they have, ask before calling either tool.

You need a real `property_id` — take it from a SearchProperty result or from earlier in
this conversation, never from a guess. `requested_slot_time` must include an explicit UTC
offset (listings here operate on UTC+07:00 unless the user says otherwise), and must be a
time the user actually chose, not one you assumed.

`client_id` identifies who the viewing is for. Leave it out when the person you are
talking to is booking for themselves; it is only meaningful when a staff member is
booking on a named client's behalf, and it is ignored otherwise.\
"""


class ScheduleViewingOutput(BaseModel):
    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    status: Literal["confirmed"]


def _resolve_client_id(ctx: RunContext[AgentDeps], client_id: str | None) -> str:
    """Decide *whose* viewing this is — from the authenticated caller, not the model.

    A ``client`` caller's ``client_id`` argument is **overridden unconditionally**: a
    client can never book on someone else's behalf no matter what the model generates
    (README Design Decisions §5). Staff may act for a client, but only for one that
    exists and is active.
    """
    user = ctx.deps.user
    if user.role == "client":
        if client_id is not None and client_id != user.id:
            logger.warning(
                "tool_client_id_overridden",
                extra={
                    "tool": TOOL_NAME,
                    "user_id": user.id,
                    "supplied_client_id": client_id,
                },
            )
        return user.id

    if client_id is None:
        # Deliberately an error rather than defaulting to the staff member's own id,
        # which would create a booking whose client is the agent conducting it.
        raise ValidationToolError(
            "A client_id is required when booking on someone else's behalf.",
            code="client_id_required",
            guidance="Ask the user which client this viewing is for.",
        )

    target = ctx.deps.db.get(User, client_id)
    if target is None or target.status != "active":
        raise ValidationToolError(
            "No active client account matches that id.",
            code="client_not_found",
            guidance="Ask the user to confirm which client this viewing is for.",
        )
    return target.id


def _assert_agent_owns_property(ctx: RunContext[AgentDeps], property_id: str) -> None:
    """An ``agent`` may only book viewings on their own listings.

    Resolved through the shared scoped query, so a listing outside the agent's
    visibility is reported the same way a nonexistent one is.
    """
    user = ctx.deps.user
    if user.role != "agent":
        return
    prop = ctx.deps.db.exec(
        scoped_property_query(user).where(col(Property.id) == property_id)
    ).first()
    if prop is None or prop.agent_id != user.id:
        raise ValidationToolError(
            "That listing is not one you can book viewings for.",
            code="property_not_bookable",
            guidance=(
                "Explain that this listing belongs to another agent, and offer to search "
                "for the user's own listings instead."
            ),
        )


async def schedule_viewing(
    ctx: RunContext[AgentDeps],
    property_id: str,
    requested_slot_time: datetime,
    client_id: str | None = None,
) -> ScheduleViewingOutput:
    """Book a new viewing appointment for a property.

    Args:
        property_id: The listing to view, exactly as returned by SearchProperty.
        requested_slot_time: The appointment time, ISO-8601 with an explicit UTC offset
            (e.g. "2026-08-08T10:00:00+07:00").
        client_id: Only for staff booking on a named client's behalf. Ignored when the
            caller is the client themselves.
    """
    if requested_slot_time.tzinfo is None:
        # Reading a naive datetime as UTC would silently move an Asia/Jakarta booking by
        # seven hours, which surfaces as the user turning up at the wrong time — worse
        # than making the model restate the time with its offset.
        raise ValidationToolError(
            "requested_slot_time must include an explicit UTC offset.",
            code="slot_time_not_timezone_aware",
            guidance=(
                "Re-send the time with an offset, e.g. 2026-08-08T10:00:00+07:00. Do not "
                "guess the offset if the user's timezone is genuinely unclear — ask."
            ),
        )

    user = ctx.deps.user
    resolved_client_id = _resolve_client_id(ctx, client_id)
    _assert_agent_owns_property(ctx, property_id)

    try:
        booking = create_booking(
            ctx.deps.db,
            property_id=property_id,
            client_id=resolved_client_id,
            requested_slot_time=requested_slot_time,
        )
    except BookingDomainError as exc:
        logger.info(
            "tool_call_rejected",
            extra={
                "tool": TOOL_NAME,
                "user_id": user.id,
                "role": user.role,
                "conversation_id": ctx.deps.conversation_id,
                "error_code": exc.code,
            },
        )
        raise as_tool_error(exc) from None
    except Exception:
        logger.exception(
            "schedule_viewing_failed",
            extra={"tool": TOOL_NAME, "user_id": user.id},
        )
        raise UpstreamToolError(
            "The booking could not be saved.",
            guidance=(
                "Apologize, confirm nothing was booked, and offer to connect the user "
                "with a human agent."
            ),
        ) from None

    # After commit, never inline in the response path (README Design Decisions §8).
    ctx.deps.notifier.publish_booking_created(
        BookingCreatedEvent(
            booking_id=booking.id,
            property_id=booking.property_id,
            client_id=booking.client_id,
            agent_id=booking.agent_id,
            slot_time=booking.slot_time,
            status=booking.status,
            created_at=booking.created_at,
        )
    )

    logger.info(
        "tool_call",
        extra={
            "tool": TOOL_NAME,
            "user_id": user.id,
            "role": user.role,
            "conversation_id": ctx.deps.conversation_id,
            "booking_id": booking.id,
            "property_id": booking.property_id,
            "client_id": booking.client_id,
        },
    )
    return ScheduleViewingOutput(
        booking_id=booking.id,
        property_id=booking.property_id,
        client_id=booking.client_id,
        agent_id=booking.agent_id,
        slot_time=booking.slot_time.astimezone(timezone.utc),
        status="confirmed",
    )
