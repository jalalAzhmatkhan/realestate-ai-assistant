from datetime import date

from pydantic import BaseModel, Field

from app.models import ListingType, PriceUnit, Property, PropertyStatus, PropertyType


class PropertySummary(BaseModel):
    """The item type of **both** the ``SearchProperty`` tool output and
    ``GET /api/v1/properties`` — one shape, so the model filters and reads results using
    one vocabulary and the dashboard renders the same fields the chat surface describes.

    ``property_type``, never bare ``type`` (X1 ruling, recorded in
    ``2026-08-05-escalation-assignment-contract.md`` § Section 2).
    """

    id: str
    title: str
    property_type: PropertyType
    listing_type: ListingType
    price: float
    currency: str
    price_unit: str
    bedrooms: int
    bathrooms: int
    area_sqm: float
    address: str
    city: str
    status: PropertyStatus
    agent_id: str | None
    # Present only when the caller supplied a geo filter — the model should not invent
    # a distance for a result that was never distance-ranked.
    distance_km: float | None = None

    @classmethod
    def from_property(
        cls, prop: Property, *, distance_km: float | None = None
    ) -> "PropertySummary":
        return cls(
            id=prop.id,
            title=prop.title,
            property_type=prop.property_type,
            listing_type=prop.listing_type,
            price=prop.price,
            currency=prop.currency,
            price_unit=prop.price_unit,
            bedrooms=prop.bedrooms,
            bathrooms=prop.bathrooms,
            area_sqm=prop.area_sqm,
            address=prop.address,
            city=prop.city,
            status=prop.status,
            agent_id=prop.agent_id,
            distance_km=distance_km,
        )


class PropertyDetail(BaseModel):
    """The whole listing, returned by the write endpoints.

    ``PropertySummary`` is deliberately lossy — it is what the *model* reads, and every
    extra field is more surface for it to hallucinate about. A ``PUT`` that answered with
    that shape would omit fields the caller just submitted, so writes get the full record.
    """

    id: str
    title: str
    property_type: PropertyType
    listing_type: ListingType
    price: float
    currency: str
    price_unit: PriceUnit
    bedrooms: int
    bathrooms: int
    area_sqm: float
    address: str
    city: str
    latitude: float
    longitude: float
    amenities: list[str]
    agent_id: str | None
    status: PropertyStatus
    description: str
    listed_date: date

    @classmethod
    def from_property(cls, prop: Property) -> "PropertyDetail":
        return cls.model_validate(prop, from_attributes=True)


class PropertyWriteRequest(BaseModel):
    """Body of both ``POST`` (create) and ``PUT`` (full replace).

    One model for both because ``PUT`` here is a genuine replacement — the admin edit
    screen submits the whole form — so a partial-update shape would misdescribe it.

    ``agent_id`` and ``status`` are *requests*, not commitments: the router forces
    ``agent_id`` to the caller's own id for an ``agent`` role, since a body field must
    never decide ownership.
    """

    title: str = Field(min_length=1, max_length=200)
    property_type: PropertyType
    listing_type: ListingType
    price: float = Field(ge=0)
    currency: str = Field(default="IDR", min_length=3, max_length=3)
    price_unit: PriceUnit
    bedrooms: int = Field(ge=0)
    bathrooms: int = Field(ge=0)
    area_sqm: float = Field(gt=0)
    address: str = Field(min_length=1, max_length=500)
    city: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    amenities: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=5000)
    agent_id: str | None = None
    # Defaults to `draft`, not `active`: a create that publishes to every client by
    # omission is the wrong direction to fail in.
    status: PropertyStatus = "draft"
    listed_date: date | None = None
