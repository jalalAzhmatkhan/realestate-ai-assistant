from datetime import datetime

from pydantic import BaseModel, Field

from app.models import Booking, BookingStatus

MAX_RESCHEDULE_REASON_LENGTH = 500


class BookingResponse(BaseModel):
    """The booking object, item type of ``GET /api/v1/bookings``.

    Field set is the README's reschedule response minus ``previous_slot_time``, which
    only means anything on a move.
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

    @classmethod
    def from_booking(cls, booking: Booking) -> "BookingResponse":
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
        )


class BookingRescheduleResponse(BookingResponse):
    previous_slot_time: datetime

    @classmethod
    def from_reschedule(
        cls, booking: Booking, previous_slot_time: datetime
    ) -> "BookingRescheduleResponse":
        return cls(
            **BookingResponse.from_booking(booking).model_dump(),
            previous_slot_time=previous_slot_time,
        )


class RescheduleBookingRequest(BaseModel):
    # No identity fields at all — scoping comes from the authenticated caller and the
    # URL path, so there is nothing here for a caller to spoof.
    requested_slot_time: datetime
    reason: str | None = Field(default=None, max_length=MAX_RESCHEDULE_REASON_LENGTH)
