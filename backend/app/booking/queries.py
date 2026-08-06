"""The RBAC-scoped booking query, shared by the reschedule tool and the REST endpoints.

The same reasoning as ``app/property/queries.py``: ``GET /api/v1/bookings``,
``POST /bookings/{id}/reschedule``, ``POST /bookings/{id}/cancel``, and the
``RescheduleViewing`` tool must agree on which bookings a caller may touch, so what a
user sees in the dashboard and what they can move in chat are the same set *by
construction* rather than by two implementations kept in step.

For the tool this scoping is more than a filter — **it is the authorization boundary**.
The resolver never searches outside it, so an out-of-scope ``booking_id`` is
indistinguishable from a nonexistent one and the agent cannot be driven to enumerate
booking IDs by asking it to reschedule guesses
(``2026-08-05-reschedule-viewing-tool-contract.md`` decision #5).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import Booking, BookingStatus, Property, User


def scoped_booking_query(user: User) -> SelectOfScalar[Booking]:
    """A ``SELECT`` over exactly the bookings ``user`` is allowed to act on.

    | Role    | Resolvable booking set                |
    |---------|---------------------------------------|
    | admin   | any booking                           |
    | agent   | ``booking.agent_id == user.id``       |
    | client  | ``booking.client_id == user.id``      |
    """
    statement = select(Booking)
    if user.role == "admin":
        return statement
    if user.role == "agent":
        return statement.where(col(Booking.agent_id) == user.id)
    return statement.where(col(Booking.client_id) == user.id)


@dataclass(frozen=True)
class BookingNames:
    """The three display strings ``BookingResponse`` denormalizes onto a booking row
    (``2026-08-06-booking-response-name-denormalization.md``): an ``agent`` or ``client``
    cannot resolve the ids themselves, since ``GET /users/{id}`` is admin-only."""

    property_title: str
    client_name: str
    agent_name: str


def resolve_booking_names(
    db: Session, bookings: Sequence[Booking]
) -> dict[str, BookingNames]:
    """Names for a whole page of bookings in two queries, keyed by booking id.

    Batched rather than per row because ``Booking`` declares no ``relationship()`` (see
    ``app/db/seed.py``) — resolving inline would be three lookups per row. Indexing the
    maps directly is safe: ``property_id``/``client_id``/``agent_id`` are all non-null
    foreign keys, so the referenced rows exist.
    """
    if not bookings:
        return {}

    titles = {
        prop.id: prop.title
        for prop in db.exec(
            select(Property).where(
                col(Property.id).in_({booking.property_id for booking in bookings})
            )
        ).all()
    }
    names = {
        user.id: user.name
        for user in db.exec(
            select(User).where(
                col(User.id).in_(
                    {booking.client_id for booking in bookings}
                    | {booking.agent_id for booking in bookings}
                )
            )
        ).all()
    }
    return {
        booking.id: BookingNames(
            property_title=titles[booking.property_id],
            client_name=names[booking.client_id],
            agent_name=names[booking.agent_id],
        )
        for booking in bookings
    }


@dataclass(frozen=True)
class BookingFilters:
    """``GET /api/v1/bookings``' filter set.

    Sequence-typed fields are the repeatable REST params (OR within one filter); the
    rest are AND-ed. Like the property filters, these compose *onto* the scoped query,
    so ``client_id=<someone else>`` empties the result set rather than widening it.
    """

    property_ids: Sequence[str] = ()
    agent_id: str | None = None
    client_id: str | None = None
    statuses: Sequence[BookingStatus] = ()
    date_from: datetime | None = None
    date_to: datetime | None = None


def apply_booking_filters(
    statement: SelectOfScalar[Booking], filters: BookingFilters
) -> SelectOfScalar[Booking]:
    if filters.property_ids:
        statement = statement.where(col(Booking.property_id).in_(list(filters.property_ids)))
    if filters.agent_id:
        statement = statement.where(col(Booking.agent_id) == filters.agent_id)
    if filters.client_id:
        statement = statement.where(col(Booking.client_id) == filters.client_id)
    if filters.statuses:
        statement = statement.where(col(Booking.status).in_(list(filters.statuses)))
    if filters.date_from is not None:
        statement = statement.where(col(Booking.slot_time) >= filters.date_from)
    if filters.date_to is not None:
        # Inclusive at both ends, per the README's "inclusive range over slot_time".
        statement = statement.where(col(Booking.slot_time) <= filters.date_to)
    return statement
