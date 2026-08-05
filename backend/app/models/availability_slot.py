from datetime import datetime
from typing import Literal

from sqlalchemy import Column, ForeignKey, String
from sqlmodel import Field, SQLModel

from app.models.types import UtcDateTime

SlotStatus = Literal["open", "booked"]


class AvailabilitySlot(SQLModel, table=True):
    __tablename__ = "availability_slots"

    id: str = Field(primary_key=True)
    agent_id: str = Field(
        sa_column=Column(String, ForeignKey("users.id"), nullable=False, index=True)
    )
    property_id: str = Field(
        sa_column=Column(String, ForeignKey("properties.id"), nullable=False, index=True)
    )
    slot_time: datetime = Field(sa_column=Column(UtcDateTime, nullable=False, index=True))
    status: SlotStatus = Field(
        default="open", sa_column=Column(String, nullable=False, index=True)
    )
