from datetime import datetime

from pydantic import BaseModel, Field

from app.booking.queries import BookingNames
from app.models import Booking, BookingStatus

MAX_RESCHEDULE_REASON_LENGTH = 500


class BookingResponse(BaseModel):
    """The booking object, item type of ``GET /api/v1/bookings``.

    Field set is the README's reschedule response minus ``previous_slot_time``, which
    only means anything on a move.

    ``property_title``/``client_name``/``agent_name`` are read-time denormalizations of
    the three ids, not stored on the booking row — the dashboard's booking screens render
    names, and an ``agent`` or ``client`` has no other way to obtain them.
    """

    booking_id: str
    property_id: str
    client_id: str
    agent_id: str
    slot_time: datetime
    availability_slot_id: str
    status: BookingStatus
    rescheduled_count: int
    updated_at: datetime
    property_title: str
    client_name: str
    agent_name: str

    @classmethod
    def from_booking(cls, booking: Booking, names: BookingNames) -> "BookingResponse":
        return cls(
            booking_id=booking.id,
            property_id=booking.property_id,
            client_id=booking.client_id,
            agent_id=booking.agent_id,
            slot_time=booking.slot_time,
            availability_slot_id=booking.availability_slot_id,
            status=booking.status,
            rescheduled_count=booking.rescheduled_count,
            updated_at=booking.updated_at,
            property_title=names.property_title,
            client_name=names.client_name,
            agent_name=names.agent_name,
        )


class BookingRescheduleResponse(BookingResponse):
    previous_slot_time: datetime

    @classmethod
    def from_reschedule(
        cls, booking: Booking, names: BookingNames, previous_slot_time: datetime
    ) -> "BookingRescheduleResponse":
        return cls(
            **BookingResponse.from_booking(booking, names).model_dump(),
            previous_slot_time=previous_slot_time,
        )


class RescheduleBookingRequest(BaseModel):
    # No identity fields at all — scoping comes from the authenticated caller and the
    # URL path, so there is nothing here for a caller to spoof.
    requested_slot_time: datetime
    reason: str | None = Field(default=None, max_length=MAX_RESCHEDULE_REASON_LENGTH)
