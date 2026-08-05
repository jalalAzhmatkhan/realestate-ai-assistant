"""Availability resolution, conflict detection, and the transactional slot swap.

This is the **single** implementation of "is this slot free" and "move this booking",
shared by all three booking write paths (README Design Decisions §9):

    | Call site                              | Surface        | Operation                  |
    |----------------------------------------|----------------|----------------------------|
    | ``schedule_viewing`` (``BookViewing``)  | Chat / LLM     | claim a slot, new booking  |
    | ``reschedule_viewing``                  | Chat / LLM     | release old, claim new     |
    | ``POST /bookings/{id}/reschedule``      | Admin SPA/REST | release old, claim new     |

The module is deliberately **pure domain logic**: it knows nothing about HTTP status
codes, FastAPI, or Pydantic AI's ``ToolError``. Failures are raised as
:class:`BookingDomainError` subclasses carrying a stable ``code``; each caller maps
that code to its own rendering (an HTTP envelope, or a ``ToolError``). The codes are
the ones the README's failure-mode tables name, so the two surfaces describe the same
condition identically.

**RBAC lives above this module.** Callers resolve *which* booking and *which* property
the user may act on (the reschedule resolver is itself the authorization boundary) and
hand a resolved ``Booking`` in. Nothing here inspects a caller identity.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from app.models import AvailabilitySlot, Booking, Property
from app.models.types import utcnow

logger = logging.getLogger(__name__)

# README: "a list of up to 3 {availability_slot_id, slot_time} objects for the same
# agent/property — the same shape schedule_viewing returns on conflict".
MAX_SUGGESTED_ALTERNATIVES = 3


@dataclass(frozen=True)
class SlotSuggestion:
    """One entry of ``suggested_alternatives``. Field names are contractual: all three
    booking surfaces render this identically, so the SPA has one component for it."""

    availability_slot_id: str
    slot_time: datetime


@dataclass(frozen=True)
class RescheduleResult:
    """What changed, for callers that must report the move.

    ``booking`` is the *same row* as the one passed in — a reschedule keeps its
    ``booking_id`` rather than producing a cancel/create pair.
    """

    booking: Booking
    previous_slot_time: datetime
    previous_availability_slot_id: str


class BookingDomainError(Exception):
    """Base for every booking-domain failure.

    ``code`` is the stable identifier both surfaces branch on; ``details`` carries
    error-specific extras (today only ``suggested_alternatives``) that a caller merges
    into its own error payload.
    """

    code: str = "booking_error"
    default_message: str = "The booking could not be completed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def details(self) -> dict[str, Any]:
        return {}


class BookingNotFoundError(BookingDomainError):
    """No booking matched within the caller's scope.

    Never raised by this module — the callers own booking resolution, since that
    resolution *is* the authorization boundary. It lives here so the REST endpoint and
    the ``RescheduleViewing`` tool cannot drift on the code string.
    """

    code = "booking_not_found"
    default_message = "No matching booking was found."


class BookingNotReschedulableError(BookingDomainError):
    code = "booking_not_reschedulable"
    default_message = "This booking can no longer be rescheduled."


class BookingNotCancellableError(BookingDomainError):
    code = "booking_not_cancellable"
    default_message = "This booking can no longer be cancelled."


class PropertyNotFoundError(BookingDomainError):
    code = "property_not_found"
    default_message = "That property could not be found."


class PropertyNotBookableError(BookingDomainError):
    code = "property_not_bookable"
    default_message = "That property is not available for viewings."


class SlotTimeInPastError(BookingDomainError):
    code = "slot_time_in_past"
    default_message = "The requested time is in the past."


class SlotUnchangedError(BookingDomainError):
    code = "slot_unchanged"
    default_message = "The booking is already at that time."


class SlotConflictError(BookingDomainError):
    """Requested slot cannot be claimed. Carries up to three alternatives so every
    surface can offer the user a next step instead of a dead end."""

    code = "slot_conflict"
    default_message = "That time is not available."

    def __init__(
        self,
        suggested_alternatives: tuple[SlotSuggestion, ...] = (),
        message: str | None = None,
    ) -> None:
        self.suggested_alternatives = suggested_alternatives
        super().__init__(message)

    @property
    def details(self) -> dict[str, Any]:
        return {
            "suggested_alternatives": [
                asdict(suggestion) for suggestion in self.suggested_alternatives
            ]
        }


class SlotUnavailableError(SlotConflictError):
    """The requested time is not in the agent's availability at all."""

    code = "slot_unavailable"
    default_message = "That time is not in the agent's availability."


class BookingSlotConflictError(SlotConflictError):
    """The slot exists but is already claimed by another booking."""

    code = "booking_slot_conflict"
    default_message = "That slot is already booked."


def _as_utc(value: datetime) -> datetime:
    """Naive input is read as UTC, matching how ``UtcDateTime`` persists it.

    Without this, an aware/naive comparison raises ``TypeError`` deep inside a
    conflict check rather than surfacing as a domain error.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def find_alternative_slots(
    db: Session,
    *,
    agent_id: str,
    property_id: str,
    now: datetime | None = None,
    limit: int = MAX_SUGGESTED_ALTERNATIVES,
) -> tuple[SlotSuggestion, ...]:
    """The soonest ``open``, still-future slots for the same agent and property.

    Past slots are excluded: offering a time that has already elapsed is worse than
    offering nothing, because the user will try to take it.
    """
    reference = _as_utc(now or utcnow())
    rows = db.exec(
        select(AvailabilitySlot)
        .where(
            AvailabilitySlot.agent_id == agent_id,
            AvailabilitySlot.property_id == property_id,
            AvailabilitySlot.status == "open",
            AvailabilitySlot.slot_time > reference,
        )
        .order_by(AvailabilitySlot.slot_time)
        .limit(limit)
    ).all()
    return tuple(
        SlotSuggestion(availability_slot_id=row.id, slot_time=row.slot_time) for row in rows
    )


def _find_slot(
    db: Session, *, agent_id: str, property_id: str, slot_time: datetime
) -> AvailabilitySlot | None:
    return db.exec(
        select(AvailabilitySlot).where(
            AvailabilitySlot.agent_id == agent_id,
            AvailabilitySlot.property_id == property_id,
            AvailabilitySlot.slot_time == slot_time,
        )
    ).first()


def _assert_claimable(
    db: Session,
    slot: AvailabilitySlot | None,
    *,
    agent_id: str,
    property_id: str,
    now: datetime | None,
) -> AvailabilitySlot:
    if slot is None:
        raise SlotUnavailableError(
            find_alternative_slots(
                db, agent_id=agent_id, property_id=property_id, now=now
            )
        )
    if slot.status != "open":
        raise BookingSlotConflictError(
            find_alternative_slots(
                db, agent_id=agent_id, property_id=property_id, now=now
            )
        )
    return slot


def resolve_slot(
    db: Session,
    *,
    agent_id: str,
    property_id: str,
    requested_slot_time: datetime,
    now: datetime | None = None,
) -> AvailabilitySlot:
    """Find the agent's slot at ``requested_slot_time`` and assert it is claimable.

    Raises :class:`SlotUnavailableError` when no such slot exists and
    :class:`BookingSlotConflictError` when it exists but is taken — two distinct
    conditions the README's failure tables keep separate, both carrying alternatives.
    """
    slot = _find_slot(
        db,
        agent_id=agent_id,
        property_id=property_id,
        slot_time=_as_utc(requested_slot_time),
    )
    return _assert_claimable(
        db, slot, agent_id=agent_id, property_id=property_id, now=now
    )


def _find_own_confirmed_booking(
    db: Session, *, availability_slot_id: str, client_id: str
) -> Booking | None:
    return db.exec(
        select(Booking).where(
            Booking.availability_slot_id == availability_slot_id,
            Booking.client_id == client_id,
            Booking.status == "confirmed",
        )
    ).first()


def _claim_slot(db: Session, slot_id: str) -> bool:
    """Conditional UPDATE, so the read-then-write window cannot double-book.

    ``WHERE status = 'open'`` makes the claim itself the check: a concurrent claimer
    updates zero rows and gets the conflict path, without needing row locks or the
    unique constraint that production volume will eventually want.
    """
    result = db.execute(
        sa_update(AvailabilitySlot)
        .where(AvailabilitySlot.id == slot_id, AvailabilitySlot.status == "open")
        .values(status="booked")
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount == 1


def _release_slot(db: Session, slot_id: str) -> None:
    db.execute(
        sa_update(AvailabilitySlot)
        .where(AvailabilitySlot.id == slot_id)
        .values(status="open")
        .execution_options(synchronize_session="fetch")
    )


def _load_bookable_property(db: Session, property_id: str) -> Property:
    prop = db.get(Property, property_id)
    if prop is None:
        raise PropertyNotFoundError()
    if prop.status != "active":
        raise PropertyNotBookableError()
    if prop.agent_id is None:
        # Nobody can conduct the viewing, and Booking.agent_id is non-nullable.
        raise PropertyNotBookableError()
    return prop


def create_booking(
    db: Session,
    *,
    property_id: str,
    client_id: str,
    requested_slot_time: datetime,
    now: datetime | None = None,
) -> Booking:
    """Claim an open slot and create the booking, in one transaction.

    **Idempotent for the same client on the same slot** (ruling A5's pre-insert
    existence check): a resubmitted request returns the caller's existing confirmed
    booking instead of reporting a conflict about their own booking. A *different*
    client requesting the same slot still gets ``booking_slot_conflict``.

    ``client_id`` must already be re-authorized by the caller (a ``client`` caller's
    LLM-supplied value is overridden with their own id upstream) — this module trusts
    the id it is handed.
    """
    reference = _as_utc(now or utcnow())
    requested = _as_utc(requested_slot_time)

    if requested <= reference:
        raise SlotTimeInPastError()

    prop = _load_bookable_property(db, property_id)
    agent_id = prop.agent_id
    assert agent_id is not None  # guaranteed by _load_bookable_property

    slot = _find_slot(
        db, agent_id=agent_id, property_id=property_id, slot_time=requested
    )

    # Checked before claimability, because the slot the caller already holds is by
    # definition no longer `open` — asserting first would report their own booking
    # back to them as a conflict.
    if slot is not None:
        existing = _find_own_confirmed_booking(
            db, availability_slot_id=slot.id, client_id=client_id
        )
        if existing is not None:
            logger.info(
                "booking_create_deduplicated",
                extra={
                    "booking_id": existing.id,
                    "property_id": existing.property_id,
                    "client_id": existing.client_id,
                    "agent_id": existing.agent_id,
                    "availability_slot_id": existing.availability_slot_id,
                    "slot_time": existing.slot_time.isoformat(),
                },
            )
            return existing

    slot = _assert_claimable(
        db, slot, agent_id=agent_id, property_id=property_id, now=reference
    )

    try:
        if not _claim_slot(db, slot.id):
            raise BookingSlotConflictError(
                find_alternative_slots(
                    db, agent_id=agent_id, property_id=property_id, now=reference
                )
            )
        booking = Booking(
            availability_slot_id=slot.id,
            property_id=property_id,
            client_id=client_id,
            agent_id=agent_id,
            slot_time=requested,
            status="confirmed",
        )
        db.add(booking)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(booking)

    logger.info(
        "booking_created",
        extra={
            "booking_id": booking.id,
            "property_id": booking.property_id,
            "client_id": booking.client_id,
            "agent_id": booking.agent_id,
            "availability_slot_id": booking.availability_slot_id,
            "slot_time": booking.slot_time.isoformat(),
        },
    )
    return booking


def reschedule_booking(
    db: Session,
    booking: Booking,
    *,
    requested_slot_time: datetime,
    now: datetime | None = None,
) -> RescheduleResult:
    """Move ``booking`` to a different slot on the same property with the same agent.

    One transaction: claim the new slot, release the old one, update the booking row —
    so a mid-way failure cannot leave a booking pointing at a released slot. The new
    slot is claimed *before* the old one is released, so a conflict leaves the original
    booking completely untouched.

    ``booking`` must already be resolved within the caller's RBAC scope; this module
    does not know who is asking.
    """
    reference = _as_utc(now or utcnow())
    requested = _as_utc(requested_slot_time)
    current_slot_time = _as_utc(booking.slot_time)

    # Cheapest-first, and pure-input validation before resource state, so a caller
    # never has to guess which of two applicable errors it will get.
    if requested <= reference:
        raise SlotTimeInPastError()
    if booking.status != "confirmed":
        raise BookingNotReschedulableError()
    if current_slot_time <= reference:
        # A viewing that has already happened is treated as completed.
        raise BookingNotReschedulableError()
    if requested == current_slot_time:
        # Explicitly a no-op: no write occurs, so the booking is not touched at all.
        raise SlotUnchangedError()

    _load_bookable_property(db, booking.property_id)

    new_slot = resolve_slot(
        db,
        agent_id=booking.agent_id,
        property_id=booking.property_id,
        requested_slot_time=requested,
        now=reference,
    )

    previous_slot_id = booking.availability_slot_id
    try:
        if not _claim_slot(db, new_slot.id):
            raise BookingSlotConflictError(
                find_alternative_slots(
                    db,
                    agent_id=booking.agent_id,
                    property_id=booking.property_id,
                    now=reference,
                )
            )
        _release_slot(db, previous_slot_id)

        booking.availability_slot_id = new_slot.id
        booking.slot_time = requested
        booking.rescheduled_count += 1
        booking.updated_at = utcnow()
        db.add(booking)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(booking)

    logger.info(
        "booking_rescheduled",
        extra={
            "booking_id": booking.id,
            "property_id": booking.property_id,
            "client_id": booking.client_id,
            "agent_id": booking.agent_id,
            "availability_slot_id": booking.availability_slot_id,
            "previous_availability_slot_id": previous_slot_id,
            "slot_time": booking.slot_time.isoformat(),
            "previous_slot_time": current_slot_time.isoformat(),
            "rescheduled_count": booking.rescheduled_count,
        },
    )
    return RescheduleResult(
        booking=booking,
        previous_slot_time=current_slot_time,
        previous_availability_slot_id=previous_slot_id,
    )


def cancel_booking(db: Session, booking: Booking) -> Booking:
    """Release the slot back to ``open`` and mark the booking ``cancelled``.

    Idempotent for an already-cancelled booking (returned untouched, no slot write) so
    a double-click or a stale tab does not surface an error. A ``completed`` booking
    raises instead — releasing its slot would advertise a viewing that already happened.
    """
    if booking.status == "cancelled":
        return booking
    if booking.status != "confirmed":
        raise BookingNotCancellableError()

    released_slot_id = booking.availability_slot_id
    try:
        _release_slot(db, released_slot_id)
        booking.status = "cancelled"
        booking.updated_at = utcnow()
        db.add(booking)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(booking)

    logger.info(
        "booking_cancelled",
        extra={
            "booking_id": booking.id,
            "property_id": booking.property_id,
            "client_id": booking.client_id,
            "agent_id": booking.agent_id,
            "availability_slot_id": released_slot_id,
            "slot_time": booking.slot_time.isoformat(),
        },
    )
    return booking
