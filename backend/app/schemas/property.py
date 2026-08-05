from pydantic import BaseModel

from app.models import ListingType, Property, PropertyStatus, PropertyType


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
