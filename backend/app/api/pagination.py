"""Shared pagination, sorting, and the list-response envelope.

``GET /api/v1/properties``, ``GET /api/v1/bookings``, and ``GET /api/v1/users`` all
answer with the same envelope and honour the same ``page``/``page_size``/``sort``
rules. Implementing that once here is what stops the three routers drifting apart
(README § "List endpoints: pagination, filtering, sorting").

Offset pagination is deliberate: the admin dashboard needs a total count and
jump-to-page navigation, and ``total`` is cheap at MVP scale. The documented upgrade
path, once a list outgrows a few thousand rows, is an *optional* ``cursor`` param
alongside ``page`` rather than a change to this envelope.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.api.deps import SettingsDep
from app.core.exceptions import InvalidSortFieldError, PageSizeTooLargeError

T = TypeVar("T")
ModelT = TypeVar("ModelT")

DESCENDING_PREFIX = "-"


class Page(BaseModel, Generic[T]):
    """The list envelope, identical for every list endpoint; only ``results``' item
    type differs. ``total`` counts matching rows *after* RBAC scoping and filters, not
    table size."""

    results: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int


@dataclass(frozen=True)
class SortOrder:
    field: str
    descending: bool


@dataclass(frozen=True)
class PageParams:
    """Validated ``page``/``page_size`` plus the *raw* ``sort`` string.

    ``sort`` stays raw because the whitelist is per-endpoint — a field valid on
    ``/properties`` is meaningless on ``/users`` — so validation happens in
    :meth:`resolve_sort`, where the caller supplies its own whitelist.
    """

    page: int
    page_size: int
    sort: str | None

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def resolve_sort(self, allowed: Mapping[str, Any], default: str) -> SortOrder:
        """Validate ``sort`` against this endpoint's whitelist, falling back to ``default``.

        An unknown field is rejected (``422 invalid_sort_field``) rather than ignored:
        silently sorting by something else returns a page the client did not ask for
        and cannot detect.
        """
        raw = self.sort or default
        descending = raw.startswith(DESCENDING_PREFIX)
        field = raw[1:] if descending else raw
        if field not in allowed:
            raise InvalidSortFieldError(
                f"Unknown sort field '{field}'. Allowed: {', '.join(sorted(allowed))}."
            )
        return SortOrder(field=field, descending=descending)


def get_page_params(
    settings: SettingsDep,
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    page_size: Annotated[
        int | None, Query(ge=1, description="Defaults to DEFAULT_PAGE_SIZE")
    ] = None,
    sort: Annotated[
        str | None,
        Query(description="Field name from this endpoint's whitelist, '-' prefix for descending"),
    ] = None,
) -> PageParams:
    if page_size is None:
        page_size = settings.default_page_size
    elif page_size > settings.max_page_size:
        raise PageSizeTooLargeError(
            f"page_size must not exceed {settings.max_page_size} (received {page_size})."
        )
    return PageParams(page=page, page_size=page_size, sort=sort)


PageParamsDep = Annotated[PageParams, Depends(get_page_params)]


def paginate(
    db: Session,
    statement: SelectOfScalar[ModelT],
    params: PageParams,
    *,
    sort_columns: Mapping[str, Any],
    default_sort: str,
    item_factory: Callable[[ModelT], T] | None = None,
) -> Page[T]:
    """Count, sort, slice, and wrap — the mechanical half every list endpoint repeats.

    ``statement`` arrives already RBAC-scoped and filtered; this function never widens
    it. A page past the end yields an empty ``results`` with the real ``total``, not a
    ``404`` — "no rows here" is a valid answer for a list.
    """
    order = params.resolve_sort(sort_columns, default_sort)
    column = sort_columns[order.field]

    total = db.exec(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).one()

    rows = db.exec(
        statement.order_by(column.desc() if order.descending else column.asc())
        .offset(params.offset)
        .limit(params.page_size)
    ).all()

    results = [item_factory(row) for row in rows] if item_factory else list(rows)
    return Page[T](
        results=results,
        page=params.page,
        page_size=params.page_size,
        total=total,
        total_pages=math.ceil(total / params.page_size),
    )
