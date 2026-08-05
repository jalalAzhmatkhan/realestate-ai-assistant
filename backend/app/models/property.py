from datetime import date
from typing import Literal
from uuid import uuid4

from sqlalchemy import JSON, Column, Date, ForeignKey, String
from sqlmodel import Field, SQLModel

PropertyType = Literal["apartment", "house", "studio", "townhouse", "villa"]
ListingType = Literal["sale", "rent"]
PropertyStatus = Literal["active", "draft", "under_offer", "sold"]
PriceUnit = Literal["per_month", "per_year", "total"]


class Property(SQLModel, table=True):
    __tablename__ = "properties"

    # Generated here, same as Booking/Escalation, so POST /api/v1/properties cannot let
    # a caller choose an id. The seed loader still supplies its own `prop-00N` values.
    id: str = Field(default_factory=lambda: f"prop-{uuid4().hex[:12]}", primary_key=True)
    title: str
    # `property_type`, never bare `type` — see the X1 ruling in
    # Documentation/audits/2026-08-05-escalation-assignment-contract.md Section 2.
    property_type: PropertyType = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    listing_type: ListingType = Field(sa_column=Column(String, nullable=False, index=True))
    price: float
    currency: str = "IDR"
    price_unit: PriceUnit = Field(sa_column=Column(String, nullable=False))
    bedrooms: int
    bathrooms: int
    area_sqm: float
    address: str
    city: str = Field(sa_column=Column(String, nullable=False, index=True))
    latitude: float
    longitude: float
    amenities: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    # Nullable: an admin-owned or unassigned listing is legal, and
    # `escalate_to_human`'s E4 branch depends on it (assignment contract, Decision 3).
    agent_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("users.id"), nullable=True, index=True),
    )
    status: PropertyStatus = Field(sa_column=Column(String, nullable=False, index=True))
    description: str
    listed_date: date = Field(sa_column=Column(Date, nullable=False, index=True))
