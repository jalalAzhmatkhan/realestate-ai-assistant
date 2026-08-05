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

from sqlmodel import col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import Booking, User


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
