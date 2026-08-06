"""``/api/v1/bookings`` — list, cancel, reschedule.

The state transitions live in ``app/booking/slots.py``, the same module the
``BookViewing`` and ``RescheduleViewing`` tools call; this router adds only booking
*resolution* and the HTTP rendering of the domain errors. Nothing about conflict
detection is reimplemented here — a fork would be the one class of bug that corrupts
data rather than returning a wrong response.

Resolution is by URL path id, scoped through ``app/booking/queries.py``. That scoping is
the authorization boundary, so a booking belonging to somebody else is ``404`` and not
``403``: booking ids cannot be enumerated by probing for a permission error.
"""

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col

from app.api.deps import CurrentUser, DbSession, NotifierDep, enforce_csrf
from app.api.pagination import Page, PageParamsDep, paginate
from app.booking.queries import (
    BookingFilters,
    apply_booking_filters,
    scoped_booking_query,
)
from app.booking.slots import BookingDomainError, cancel_booking, reschedule_booking
from app.core.exceptions import (
    BookingNotFoundError,
    DomainError,
    InvalidDateRangeError,
)
from app.models import Booking, BookingStatus, User
from app.notifications.port import BookingCancelledEvent, BookingRescheduledEvent
from app.schemas.booking import (
    BookingRescheduleResponse,
    BookingResponse,
    RescheduleBookingRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/bookings", tags=["bookings"], dependencies=[Depends(enforce_csrf)]
)

BOOKING_SORT_COLUMNS = {
    "slot_time": col(Booking.slot_time),
    "created_at": col(Booking.created_at),
    "status": col(Booking.status),
}
DEFAULT_BOOKING_SORT = "-slot_time"

# The REST rendering of app/booking/slots.py's domain codes, per the README's failure
# tables. app/agent/tools/booking_common.py is the chat surface's rendering of the same
# exceptions — two presentations, one source of truth for the conditions themselves.
_HTTP_STATUS_BY_CODE: dict[str, int] = {
    "booking_not_found": 404,
    "booking_not_reschedulable": 409,
    "booking_not_cancellable": 409,
    "slot_time_in_past": 422,
    "slot_unchanged": 422,
    "slot_unavailable": 409,
    "booking_slot_conflict": 409,
    "property_not_bookable": 409,
    # The booking's own property vanished — a server-side inconsistency, not a bad id
    # in the request, so it is not rendered as a 404 on the booking.
    "property_not_found": 409,
}
_FALLBACK_STATUS = 409


class BookingRequestError(DomainError):
    """Carries a ``slots.py`` code and its HTTP status straight through, including
    ``suggested_alternatives``, so the SPA renders a chat and a dashboard conflict with
    one component."""

    def __init__(self, exc: BookingDomainError) -> None:
        self.code = exc.code
        self.status_code = _HTTP_STATUS_BY_CODE.get(exc.code, _FALLBACK_STATUS)
        super().__init__(exc.message, **exc.details)


def _resolve_booking(db: Session, user: User, booking_id: str) -> Booking:
    booking = db.exec(
        scoped_booking_query(user).where(col(Booking.id) == booking_id)
    ).first()
    if booking is None:
        # Deliberately the same answer as "no such booking".
        raise BookingNotFoundError()
    return booking


def _log(event: str, user: User, booking: Booking, **extra: Any) -> None:
    logger.info(
        event,
        extra={
            "user_id": user.id,
            "role": user.role,
            "booking_id": booking.id,
            "property_id": booking.property_id,
            **extra,
        },
    )


@router.get("", response_model=Page[BookingResponse])
def list_bookings(
    user: CurrentUser,
    db: DbSession,
    params: PageParamsDep,
    property_id: Annotated[list[str] | None, Query(description="Repeatable")] = None,
    agent_id: Annotated[str | None, Query()] = None,
    client_id: Annotated[str | None, Query()] = None,
    status: Annotated[list[BookingStatus] | None, Query(description="Repeatable")] = None,
    date_from: Annotated[datetime | None, Query(description="Inclusive, over slot_time")] = None,
    date_to: Annotated[datetime | None, Query(description="Inclusive, over slot_time")] = None,
) -> Page[BookingResponse]:
    """List the bookings this caller may see."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRangeError()

    statement = apply_booking_filters(
        scoped_booking_query(user),
        BookingFilters(
            property_ids=tuple(property_id or ()),
            agent_id=agent_id,
            client_id=client_id,
            statuses=tuple(status or ()),
            date_from=date_from,
            date_to=date_to,
        ),
    )

    page = paginate(
        db,
        statement,
        params,
        sort_columns=BOOKING_SORT_COLUMNS,
        default_sort=DEFAULT_BOOKING_SORT,
        item_factory=BookingResponse.from_booking,
    )
    logger.info(
        "bookings_listed",
        extra={"user_id": user.id, "role": user.role, "total": page.total},
    )
    return page


@router.get("/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: str, user: CurrentUser, db: DbSession) -> BookingResponse:
    """One booking, in the same shape a list row carries — bookings have no
    summary/detail split, the list item type is already the complete record.

    Resolved through the scope that also governs cancel and reschedule, so a booking
    belonging to somebody else is ``404``, never ``403``.
    """
    booking = _resolve_booking(db, user, booking_id)

    _log("booking_read", user, booking)
    return BookingResponse.from_booking(booking)


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
def cancel(
    booking_id: str, user: CurrentUser, db: DbSession, notifier: NotifierDep
) -> BookingResponse:
    """Cancel a viewing and release its slot.

    Idempotent for an already-cancelled booking, so a double-click or a stale tab does
    not surface an error.
    """
    booking = _resolve_booking(db, user, booking_id)
    was_cancelled = booking.status == "cancelled"

    try:
        cancel_booking(db, booking)
    except BookingDomainError as exc:
        _log("booking_cancel_rejected", user, booking, error_code=exc.code)
        raise BookingRequestError(exc) from None

    if not was_cancelled:
        # After commit, never inside the request's transaction: a failed notification
        # must not roll back a cancellation that already happened.
        notifier.publish_booking_cancelled(
            BookingCancelledEvent(
                booking_id=booking.id,
                property_id=booking.property_id,
                client_id=booking.client_id,
                agent_id=booking.agent_id,
                slot_time=booking.slot_time,
                status=booking.status,
                cancelled_at=booking.updated_at,
            )
        )

    _log("booking_cancelled_via_rest", user, booking, already_cancelled=was_cancelled)
    return BookingResponse.from_booking(booking)


@router.post("/{booking_id}/reschedule", response_model=BookingRescheduleResponse)
def reschedule(
    booking_id: str,
    payload: RescheduleBookingRequest,
    user: CurrentUser,
    db: DbSession,
    notifier: NotifierDep,
) -> BookingRescheduleResponse:
    """Move a viewing to a different slot, keeping the same ``booking_id``."""
    booking = _resolve_booking(db, user, booking_id)

    try:
        result = reschedule_booking(
            db, booking, requested_slot_time=payload.requested_slot_time
        )
    except BookingDomainError as exc:
        _log("booking_reschedule_rejected", user, booking, error_code=exc.code)
        raise BookingRequestError(exc) from None

    moved = result.booking
    # Identical payload to the RescheduleViewing tool's, so a downstream consumer cannot
    # tell which surface moved the booking.
    notifier.publish_booking_rescheduled(
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
            reason=payload.reason,
        )
    )

    _log(
        "booking_rescheduled_via_rest",
        user,
        moved,
        rescheduled_count=moved.rescheduled_count,
    )
    return BookingRescheduleResponse.from_reschedule(moved, result.previous_slot_time)
