"""``search_property``, exposed to the LLM as ``SearchProperty`` — structured + geo search.

RBAC note: the input schema has **no identity argument**. What the caller may see comes
from ``ctx.deps.user`` through ``app/property/queries.py``'s shared scoped query — the
same one ``GET /api/v1/properties`` and ``escalate_to_human``'s assignment lookup use, so
the chat and dashboard surfaces cannot disagree about visibility.
"""

import logging
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from sqlmodel import col

from app.agent.deps import AgentDeps
from app.agent.tools.errors import UpstreamToolError
from app.models import Property
from app.property.queries import (
    PropertyFilters,
    apply_property_filters,
    haversine_km,
    scoped_property_query,
)
from app.schemas.property import PropertySummary

logger = logging.getLogger(__name__)

TOOL_NAME = "SearchProperty"
MAX_LIMIT = 25
# Bounds the Python-side Haversine pass. The scoped SQL filters run first, so this caps
# how many rows the non-scalable geo stub ever touches (see queries.haversine_km).
GEO_CANDIDATE_CAP = 500

TOOL_DESCRIPTION = """\
Search the property listings the current user is allowed to see, by any combination of
city, listing type, property type, price range, bedrooms, keywords, and proximity to a
coordinate.

Use this whenever the user is looking for a place — including vague requests ("something
near the office under 5 juta") — by translating what they said into the filters below.
Prefer fewer, well-chosen filters: over-constraining returns nothing and makes the user
repeat themselves. If the result set is empty, that is a normal successful answer, not an
error; say nothing matched and suggest relaxing a specific constraint.

You must call this before claiming any listing exists, and you must only describe
listings this tool returned. Never state a price, address, or availability that did not
come back in a result — visibility is enforced server-side per user, so a listing you do
not see here is one this user is not allowed to know about.

Call this before BookViewing when you do not already have a concrete `property_id` from
the conversation: booking needs a real id, and guessing one fails the booking.\
"""


class SearchPropertyOutput(BaseModel):
    results: list[PropertySummary]
    count: int


async def search_property(
    ctx: RunContext[AgentDeps],
    city: str | None = None,
    listing_type: Literal["sale", "rent"] | None = None,
    property_type: Literal["apartment", "house", "studio", "townhouse", "villa"] | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    bedrooms: int | None = None,
    near_lat: float | None = None,
    near_lng: float | None = None,
    radius_km: float | None = None,
    keywords: str | None = None,
    limit: int = 10,
) -> SearchPropertyOutput:
    """Search property listings visible to the current user.

    Args:
        city: City name, matched exactly (e.g. "Jakarta Selatan", "Bandung").
        listing_type: Whether the user wants to buy ("sale") or rent ("rent").
        property_type: Kind of dwelling.
        min_price: Lowest acceptable price, in the listing currency (IDR).
        max_price: Highest acceptable price, in the listing currency (IDR).
        bedrooms: Minimum number of bedrooms.
        near_lat: Latitude to search around. Supply together with near_lng.
        near_lng: Longitude to search around. Supply together with near_lat.
        radius_km: Maximum distance in kilometres from near_lat/near_lng. Ignored
            unless both coordinates are supplied.
        keywords: Free text matched against title, description, and address — use it for
            qualities that are not their own filter ("pool", "furnished", "near MRT").
        limit: Maximum number of listings to return.
    """
    user = ctx.deps.user
    effective_limit = max(1, min(limit, MAX_LIMIT))

    statement = apply_property_filters(
        scoped_property_query(user),
        PropertyFilters(
            text=keywords,
            cities=(city,) if city else (),
            listing_type=listing_type,
            property_types=(property_type,) if property_type else (),
            min_price=min_price,
            max_price=max_price,
            bedrooms=bedrooms,
        ),
    )

    geo_requested = near_lat is not None and near_lng is not None
    # Without a geo filter the DB does the limiting; with one, the Haversine pass has to
    # see every scoped candidate before any can be discarded, so the cap moves up.
    statement = statement.order_by(col(Property.listed_date).desc()).limit(
        GEO_CANDIDATE_CAP if geo_requested else effective_limit
    )

    try:
        rows = list(ctx.deps.db.exec(statement).all())
    except Exception:
        logger.exception(
            "search_property_failed",
            extra={"tool": TOOL_NAME, "user_id": user.id},
        )
        raise UpstreamToolError(
            "Property search is temporarily unavailable.",
            guidance="Apologize and offer to connect the user with a human agent.",
        ) from None

    if geo_requested:
        assert near_lat is not None and near_lng is not None  # narrowed by geo_requested
        scored = [
            (prop, haversine_km(near_lat, near_lng, prop.latitude, prop.longitude))
            for prop in rows
        ]
        if radius_km is not None:
            scored = [pair for pair in scored if pair[1] <= radius_km]
        scored.sort(key=lambda pair: pair[1])
        results = [
            PropertySummary.from_property(prop, distance_km=round(distance, 2))
            for prop, distance in scored[:effective_limit]
        ]
    else:
        results = [PropertySummary.from_property(prop) for prop in rows]

    logger.info(
        "tool_call",
        extra={
            "tool": TOOL_NAME,
            "user_id": user.id,
            "role": user.role,
            "conversation_id": ctx.deps.conversation_id,
            "geo_filtered": geo_requested,
            "result_count": len(results),
        },
    )
    # No results is an empty list, never an error — "nothing matched" is information the
    # model should relay, not a failure it should apologize for.
    return SearchPropertyOutput(results=results, count=len(results))
