"""The **single** RBAC-scoped property query, plus the filters layered on top of it.

Three surfaces must agree on what a given user may see (README Design Decisions §9, and
the sequencing checkpoint's Section B note 2):

    | Consumer                                | Uses                              |
    |-----------------------------------------|-----------------------------------|
    | ``SearchProperty`` tool (chat / LLM)     | scoped query + filters + geo      |
    | ``GET /api/v1/properties`` (admin SPA)   | scoped query + filters            |
    | ``escalate_to_human`` assignment lookup  | ``find_visible_property``         |

Three implementations kept in sync by convention is an *authorization* bug waiting to
happen, not a style problem: the chat surface and the dashboard disagreeing about
visibility means one of them is leaking. Hence one builder, consumed by all three.

**Filters narrow, they never widen.** ``apply_property_filters`` composes onto whatever
``scoped_property_query`` produced, so an ``agent_id`` filter naming another agent
yields an empty result set rather than that agent's drafts.
"""

import math
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import or_
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models import ListingType, Property, PropertyStatus, PropertyType, User

EARTH_RADIUS_KM = 6371.0


def scoped_property_query(user: User) -> SelectOfScalar[Property]:
    """A ``SELECT`` over exactly the properties ``user`` is allowed to see.

    | Role    | Visibility                                                        |
    |---------|-------------------------------------------------------------------|
    | client  | ``status="active"`` only                                          |
    | agent   | every ``active`` listing, plus their own in any status            |
    | admin   | unrestricted                                                      |

    Derived from the authenticated user only. No caller — and specifically no
    LLM-supplied argument — can influence this; ``SearchProperty``'s input schema has no
    identity field at all for exactly that reason.
    """
    statement = select(Property)
    if user.role == "admin":
        return statement
    if user.role == "agent":
        return statement.where(
            or_(col(Property.status) == "active", col(Property.agent_id) == user.id)
        )
    return statement.where(col(Property.status) == "active")


@dataclass(frozen=True)
class PropertyFilters:
    """Filter set shared by the tool and the REST list endpoint.

    Sequence-typed fields are the repeatable REST params (``?status=a&status=b``,
    meaning OR within one filter); scalar fields are AND-ed with everything else.
    """

    text: str | None = None
    cities: Sequence[str] = ()
    statuses: Sequence[PropertyStatus] = ()
    property_types: Sequence[PropertyType] = ()
    listing_type: ListingType | None = None
    agent_id: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    bedrooms: int | None = None


def apply_property_filters(
    statement: SelectOfScalar[Property], filters: PropertyFilters
) -> SelectOfScalar[Property]:
    if filters.text:
        pattern = f"%{filters.text.strip()}%"
        statement = statement.where(
            or_(
                col(Property.title).ilike(pattern),
                col(Property.description).ilike(pattern),
                col(Property.address).ilike(pattern),
            )
        )
    if filters.cities:
        statement = statement.where(col(Property.city).in_(list(filters.cities)))
    if filters.statuses:
        # Intersected with, never a substitute for, the role scoping already applied —
        # a client filtering for status="draft" gets nothing, not a draft.
        statement = statement.where(col(Property.status).in_(list(filters.statuses)))
    if filters.property_types:
        statement = statement.where(
            col(Property.property_type).in_(list(filters.property_types))
        )
    if filters.listing_type:
        statement = statement.where(col(Property.listing_type) == filters.listing_type)
    if filters.agent_id:
        statement = statement.where(col(Property.agent_id) == filters.agent_id)
    if filters.min_price is not None:
        statement = statement.where(col(Property.price) >= filters.min_price)
    if filters.max_price is not None:
        statement = statement.where(col(Property.price) <= filters.max_price)
    if filters.bedrooms is not None:
        statement = statement.where(col(Property.bedrooms) >= filters.bedrooms)
    return statement


def find_visible_property(db: Session, user: User, property_id: str) -> Property | None:
    """Load one property **through the caller's scope**, not by primary key.

    Used by ``escalate_to_human``'s listing-agent assignment. A bare ``db.get`` there
    would let a client probe for the existence and owner of a ``draft``/``sold`` listing
    by watching whether an assignee came back — the same enumeration oracle the
    404-not-403 booking posture closes
    (``2026-08-05-escalation-assignment-contract.md`` Decision 2).
    """
    statement = scoped_property_query(user).where(col(Property.id) == property_id)
    return db.exec(statement).first()


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres.

    **A documented non-scalable stub.** Computed in Python over the full scoped result
    set, which is fine for 15 seeded listings and wrong for 15,000. The production path
    is PostGIS ``ST_DWithin``/``ST_Distance`` once the Property Service is extracted
    (README Design Decisions §7); this exists so the geo *contract* is real in the MVP
    while the storage layer stays SQLite-portable.
    """
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
