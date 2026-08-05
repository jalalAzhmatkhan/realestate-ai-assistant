from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import Column, ForeignKey, String
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime, utcnow

BookingStatus = Literal["confirmed", "cancelled", "completed"]


class Booking(SQLModel, table=True):
    __tablename__ = "bookings"

    id: str = Field(default_factory=lambda: f"booking-{uuid4().hex[:12]}", primary_key=True)
    availability_slot_id: str = Field(
        sa_column=Column(
            String, ForeignKey("availability_slots.id"), nullable=False, index=True
        )
    )
    property_id: str = Field(
        sa_column=Column(String, ForeignKey("properties.id"), nullable=False, index=True)
    )
    client_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    agent_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    slot_time: datetime = Field(sa_column=Column(UtcDateTime, nullable=False, index=True))
    status: BookingStatus = Field(
        default="confirmed", sa_column=Column(String, nullable=False, index=True)
    )
    # A reschedule keeps the same booking_id and increments this instead of
    # producing a cancel+create pair (README Design Decisions §9).
    rescheduled_count: int = 0
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False, index=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UtcDateTime, nullable=False)
    )
