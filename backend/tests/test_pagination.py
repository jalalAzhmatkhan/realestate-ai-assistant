"""app/api/pagination.py — the one envelope and one param set every list endpoint shares.

`/properties`, `/bookings`, and `/users` all route through this module, so a defect
here is a defect on all three at once (README § "List endpoints").

The dependency is exercised through a real app rather than by calling
`get_page_params` directly, because `page`/`page_size` bounds are enforced by
FastAPI's own validation and the over-ceiling rejection is rendered by the app's
`DomainError` handler — neither is visible from a bare function call.
"""

import math

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.pagination import (
    Page,
    PageParams,
    PageParamsDep,
    SortOrder,
    paginate,
)
from app.core.exceptions import InvalidSortFieldError, PageSizeTooLargeError
from app.db.seed import seed_if_empty
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import Property

from .conftest import make_db_settings

PROPERTY_SORTS = {
    "price": Property.price,
    "listed_date": Property.listed_date,
    "title": Property.title,
}


class PropertyRow(BaseModel):
    id: str
    title: str
    price: float


def _register_probe_routes(app: FastAPI) -> None:
    @app.get("/_probe/params")
    def read_params(params: PageParamsDep) -> dict:
        return {
            "page": params.page,
            "page_size": params.page_size,
            "sort": params.sort,
            "offset": params.offset,
        }

    @app.get("/_probe/sort")
    def read_sort(params: PageParamsDep) -> dict:
        order = params.resolve_sort(PROPERTY_SORTS, "listed_date")
        return {"field": order.field, "descending": order.descending}

    @app.get("/_probe/properties", response_model=Page[PropertyRow])
    def list_properties(params: PageParamsDep) -> Page[PropertyRow]:
        with Session(app.state.engine) as db:
            return paginate(
                db,
                select(Property),
                params,
                sort_columns=PROPERTY_SORTS,
                default_sort="listed_date",
                item_factory=lambda row: PropertyRow(
                    id=row.id, title=row.title, price=row.price
                ),
            )


@pytest.fixture
def client_factory(tmp_path):
    from fastapi.testclient import TestClient

    clients = []

    def build(**overrides):
        settings = make_db_settings(tmp_path, **overrides)
        # Schema creation is `alembic upgrade head`'s job (see infra/backend/
        # Dockerfile), not app/main.py's lifespan — this mimics that step having
        # already run before the app starts, same pattern as test_deps.py's
        # `settings` fixture and test_auth.py.
        create_tables(build_engine(settings))
        app = create_app(settings)
        _register_probe_routes(app)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory):
    return client_factory()


@pytest.fixture
def db(tmp_path):
    engine = build_engine(make_db_settings(tmp_path))
    create_tables(engine)
    seed_if_empty(engine, make_db_settings(tmp_path))
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def total_properties(db) -> int:
    return len(db.exec(select(Property)).all())


def _params(page: int = 1, page_size: int = 20, sort: str | None = None) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort=sort)


def _detail(response) -> dict:
    return response.json()["detail"]


def _page(db, **kwargs) -> Page:
    return paginate(
        db,
        kwargs.pop("statement", select(Property)),
        kwargs.pop("params", _params()),
        sort_columns=PROPERTY_SORTS,
        default_sort=kwargs.pop("default_sort", "listed_date"),
        **kwargs,
    )


# --- get_page_params: defaults and bounds -------------------------------------


def test_defaults_come_from_settings(client):
    body = client.get("/_probe/params").json()

    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["sort"] is None


def test_a_custom_default_page_size_is_honored(client_factory):
    client = client_factory(default_page_size=7)

    assert client.get("/_probe/params").json()["page_size"] == 7


def test_explicit_values_are_honored(client):
    body = client.get("/_probe/params?page=3&page_size=5&sort=-price").json()

    assert (body["page"], body["page_size"], body["sort"]) == (3, 5, "-price")


def test_offset_is_derived_from_page_and_page_size(client):
    assert client.get("/_probe/params?page=1&page_size=20").json()["offset"] == 0
    assert client.get("/_probe/params?page=3&page_size=20").json()["offset"] == 40


def test_page_size_at_the_ceiling_is_accepted(client):
    response = client.get("/_probe/params?page_size=100")

    assert response.status_code == 200
    assert response.json()["page_size"] == 100


def test_page_size_above_the_ceiling_is_rejected(client):
    response = client.get("/_probe/params?page_size=101")

    assert response.status_code == 422
    assert _detail(response)["code"] == "page_size_too_large"


def test_an_over_ceiling_page_size_is_never_silently_clamped(client):
    """A clamped page looks to the client like the full page it asked for, so it
    stops paginating and silently loses rows — hence 422, not a quiet 100."""
    response = client.get("/_probe/params?page_size=101")

    assert "page_size" not in response.json()
    assert _detail(response)["message"].count("100") >= 1
    assert "101" in _detail(response)["message"]


def test_a_custom_ceiling_is_enforced(client_factory):
    client = client_factory(default_page_size=5, max_page_size=5)

    assert client.get("/_probe/params?page_size=5").status_code == 200
    assert client.get("/_probe/params?page_size=6").status_code == 422


@pytest.mark.parametrize("query", ["page=0", "page=-1", "page_size=0", "page_size=-1"])
def test_out_of_range_values_are_rejected_by_native_validation(client, query):
    response = client.get(f"/_probe/params?{query}")

    assert response.status_code == 422
    # FastAPI's field-level list, deliberately left intact for the SPA.
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("query", ["page=abc", "page_size=2.5"])
def test_non_integer_values_are_rejected(client, query):
    assert client.get(f"/_probe/params?{query}").status_code == 422


def test_page_size_too_large_is_a_422_domain_error():
    assert PageSizeTooLargeError.code == "page_size_too_large"
    assert PageSizeTooLargeError.status_code == 422


def test_invalid_sort_field_is_a_422_domain_error():
    assert InvalidSortFieldError.code == "invalid_sort_field"
    assert InvalidSortFieldError.status_code == 422


# --- resolve_sort -------------------------------------------------------------


def test_no_sort_falls_back_to_the_endpoint_default():
    order = _params().resolve_sort(PROPERTY_SORTS, "listed_date")

    assert order == SortOrder(field="listed_date", descending=False)


def test_a_plain_field_sorts_ascending():
    assert _params(sort="price").resolve_sort(PROPERTY_SORTS, "listed_date") == SortOrder(
        field="price", descending=False
    )


def test_a_dash_prefix_sorts_descending():
    assert _params(sort="-price").resolve_sort(PROPERTY_SORTS, "listed_date") == SortOrder(
        field="price", descending=True
    )


def test_a_descending_default_is_honored():
    assert _params().resolve_sort(PROPERTY_SORTS, "-price") == SortOrder(
        field="price", descending=True
    )


@pytest.mark.parametrize("sort", ["hacked", "-hacked", "-", "id"])
def test_an_unknown_sort_field_is_rejected(sort):
    """Silently sorting by something else returns a page the client did not ask
    for and cannot detect."""
    with pytest.raises(InvalidSortFieldError) as error:
        _params(sort=sort).resolve_sort(PROPERTY_SORTS, "listed_date")

    assert error.value.code == "invalid_sort_field"


def test_the_rejection_message_lists_the_allowed_fields():
    with pytest.raises(InvalidSortFieldError) as error:
        _params(sort="hacked").resolve_sort(PROPERTY_SORTS, "listed_date")

    message = error.value.message
    assert "hacked" in message
    for field in PROPERTY_SORTS:
        assert field in message


def test_an_unknown_sort_field_renders_as_422_over_http(client):
    response = client.get("/_probe/sort?sort=hacked")

    assert response.status_code == 422
    assert _detail(response)["code"] == "invalid_sort_field"


def test_a_known_sort_field_resolves_over_http(client):
    assert client.get("/_probe/sort?sort=-price").json() == {
        "field": "price",
        "descending": True,
    }


def test_page_params_is_immutable():
    with pytest.raises(Exception):
        _params().page = 99


# --- paginate: counting -------------------------------------------------------


def test_total_is_the_true_row_count_regardless_of_page_size(db, total_properties):
    assert _page(db, params=_params(page_size=2)).total == total_properties
    assert _page(db, params=_params(page_size=100)).total == total_properties


def test_total_is_unaffected_by_which_page_was_asked_for(db, total_properties):
    assert _page(db, params=_params(page=2, page_size=2)).total == total_properties


def test_total_pages_is_the_ceiling_of_total_over_page_size(db, total_properties):
    for page_size in (1, 2, 4, 7, 100):
        page = _page(db, params=_params(page_size=page_size))
        assert page.total_pages == math.ceil(total_properties / page_size), page_size


def test_an_empty_result_set_has_zero_total_pages(db):
    page = _page(db, statement=select(Property).where(Property.city == "Atlantis"))

    assert page.total == 0
    assert page.total_pages == 0
    assert page.results == []


def test_total_reflects_the_scoped_statement_not_the_table(db, total_properties):
    """`total` counts matching rows AFTER RBAC scoping and filters — a widened
    count would leak how many rows the caller may not see."""
    page = _page(db, statement=select(Property).where(Property.status == "active"))

    assert 0 < page.total < total_properties


# --- paginate: slicing --------------------------------------------------------


def test_a_full_page_returns_exactly_page_size_rows(db):
    assert len(_page(db, params=_params(page_size=3)).results) == 3


def test_the_last_page_returns_the_remainder(db, total_properties):
    page_size = 4
    last = math.ceil(total_properties / page_size)

    page = _page(db, params=_params(page=last, page_size=page_size))

    assert len(page.results) == total_properties - (last - 1) * page_size


def test_a_page_beyond_the_end_is_an_empty_page_not_an_error(db, total_properties):
    page = _page(db, params=_params(page=99, page_size=5))

    assert page.results == []
    assert page.total == total_properties
    assert page.page == 99
    assert page.page_size == 5


def test_walking_every_page_yields_each_row_exactly_once(db, total_properties):
    page_size = 4
    seen = []
    for number in range(1, math.ceil(total_properties / page_size) + 1):
        seen.extend(
            row.id
            for row in _page(db, params=_params(page=number, page_size=page_size)).results
        )

    assert len(seen) == total_properties
    assert len(set(seen)) == total_properties


# --- paginate: sorting --------------------------------------------------------


def test_ascending_sort_is_actually_applied(db):
    prices = [
        row.price for row in _page(db, params=_params(sort="price", page_size=100)).results
    ]

    assert prices == sorted(prices)


def test_descending_sort_is_actually_applied(db):
    prices = [
        row.price
        for row in _page(db, params=_params(sort="-price", page_size=100)).results
    ]

    assert prices == sorted(prices, reverse=True)
    assert prices != sorted(prices)


def test_the_default_sort_is_applied_when_none_is_requested(db):
    dates = [row.listed_date for row in _page(db, params=_params(page_size=100)).results]

    assert dates == sorted(dates)


def test_sorting_orders_across_page_boundaries_not_just_within_a_page(db):
    first = _page(db, params=_params(page=1, page_size=5, sort="price")).results
    second = _page(db, params=_params(page=2, page_size=5, sort="price")).results

    assert first[-1].price <= second[0].price


def test_an_unknown_sort_field_fails_before_any_rows_are_read(db):
    with pytest.raises(InvalidSortFieldError):
        _page(db, params=_params(sort="hacked"))


# --- paginate: the envelope ---------------------------------------------------


def test_the_envelope_carries_exactly_the_documented_keys(db):
    page = _page(db, params=_params(page_size=2))

    assert set(page.model_dump()) == {
        "results",
        "page",
        "page_size",
        "total",
        "total_pages",
    }


def test_the_envelope_echoes_back_the_requested_page_and_size(db):
    page = _page(db, params=_params(page=2, page_size=3))

    assert (page.page, page.page_size) == (2, 3)


def test_item_factory_shapes_the_results(db):
    page = _page(
        db,
        params=_params(page_size=2),
        item_factory=lambda row: PropertyRow(id=row.id, title=row.title, price=row.price),
    )

    assert all(isinstance(item, PropertyRow) for item in page.results)


def test_without_an_item_factory_the_rows_pass_through(db):
    page = _page(db, params=_params(page_size=2))

    assert all(isinstance(item, Property) for item in page.results)


# --- paginate over HTTP -------------------------------------------------------


def test_a_route_serializes_the_envelope(client):
    body = client.get("/_probe/properties?page_size=3&sort=-price").json()

    assert set(body) == {"results", "page", "page_size", "total", "total_pages"}
    assert len(body["results"]) == 3
    assert set(body["results"][0]) == {"id", "title", "price"}


def test_a_route_page_beyond_the_end_is_200_with_an_empty_list(client):
    response = client.get("/_probe/properties?page=99")

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["total"] > 0


def test_a_route_rejects_an_over_ceiling_page_size_before_querying(client):
    response = client.get("/_probe/properties?page_size=1000")

    assert response.status_code == 422
    assert _detail(response)["code"] == "page_size_too_large"


def test_a_route_rejects_an_unknown_sort_field(client):
    response = client.get("/_probe/properties?sort=agent_id")

    assert response.status_code == 422
    assert _detail(response)["code"] == "invalid_sort_field"
