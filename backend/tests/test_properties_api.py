"""``/api/v1/properties`` against the README's list/write contract.

The properties surface is where the read scope (``scoped_property_query``) and the
narrower write scope (``editable_property_query``) diverge, so most of this file is about
that asymmetry: an ``agent`` can *read* every active listing and *edit* only their own,
and the difference must answer ``404``, never ``403``.

Also the home of the shared REST-test plumbing (login, cookie/CSRF, code extraction)
that ``test_bookings_api.py`` and ``test_users_api.py`` import — one login helper rather
than three, matching how ``test_chat_api.py`` imports its doubles from ``test_agent.py``.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import Property

from .conftest import SEED_PASSWORD, make_db_settings

PROPERTIES = "/api/v1/properties"
LOGIN = "/api/v1/auth/login"

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT1_EMAIL = "siti.agent@evdekimi.test"
AGENT2_EMAIL = "budi.agent@evdekimi.test"
AGENT3_EMAIL = "ayu.agent@evdekimi.test"
CLIENT_EMAIL = "andi.client@evdekimi.test"

ADMIN, AGENT1, AGENT2, AGENT3 = "u-admin-1", "u-agent-1", "u-agent-2", "u-agent-3"
CLIENT = "u-client-1"

# From backend/seed_data/properties.json. Named rather than recomputed so a seed change
# that silently alters visibility fails a test instead of a test quietly following it.
ACTIVE_IDS = frozenset(f"prop-{n:03d}" for n in range(1, 13))
DRAFT_OF_AGENT3 = "prop-013"
SOLD_OF_AGENT2 = "prop-014"
UNDER_OFFER_OF_AGENT1 = "prop-015"
ACTIVE_OF_AGENT1 = "prop-001"
ACTIVE_OF_AGENT3 = "prop-004"


# ------------------------------------------------------------------ shared plumbing


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    # Mirrors `alembic upgrade head` having already run; app/main.py's lifespan seeds
    # but deliberately does not create tables.
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def bearer(client, email=ADMIN_EMAIL) -> dict[str, str]:
    """An API-client session: a Bearer JWT, which carries no CSRF claim at all."""
    response = client.post(LOGIN, json={"email": email, "password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def cookie_login(client, email=ADMIN_EMAIL) -> str:
    """A browser session: the cookie lands in the client's jar, the CSRF token is
    returned for the caller to echo in ``X-CSRF-Token``."""
    response = client.post(
        LOGIN, json={"email": email, "password": SEED_PASSWORD, "client_type": "browser"}
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def ids_of(response) -> set[str]:
    return {item["id"] for item in response.json()["results"]}


def property_payload(**overrides) -> dict:
    return {
        "title": "Test Listing",
        "property_type": "apartment",
        "listing_type": "rent",
        "price": 8_000_000,
        "price_unit": "per_month",
        "bedrooms": 2,
        "bathrooms": 1,
        "area_sqm": 55.0,
        "address": "Jl. Test 1",
        "city": "Jakarta Selatan",
        "latitude": -6.26,
        "longitude": 106.79,
        "amenities": ["ac"],
        "description": "A test listing.",
        **overrides,
    }


# --------------------------------------------------------------- read scope (RBAC)


def test_a_client_sees_only_active_listings(client):
    response = client.get(PROPERTIES, headers=bearer(client, CLIENT_EMAIL))

    assert response.status_code == 200, response.text
    assert ids_of(response) == set(ACTIVE_IDS)
    assert response.json()["total"] == len(ACTIVE_IDS)


def test_an_agent_sees_every_active_listing_plus_their_own_non_active_ones(client):
    """``agent-1`` owns the ``under_offer`` listing; ``agent-2``'s ``sold`` and
    ``agent-3``'s ``draft`` stay hidden from them."""
    response = client.get(PROPERTIES, headers=bearer(client, AGENT1_EMAIL))

    assert ids_of(response) == set(ACTIVE_IDS) | {UNDER_OFFER_OF_AGENT1}


@pytest.mark.parametrize(
    ("email", "own_hidden_listing"),
    [
        (AGENT2_EMAIL, SOLD_OF_AGENT2),
        (AGENT3_EMAIL, DRAFT_OF_AGENT3),
    ],
)
def test_each_agents_own_non_active_listing_is_visible_only_to_them(
    client, email, own_hidden_listing
):
    visible = ids_of(client.get(PROPERTIES, headers=bearer(client, email)))

    assert own_hidden_listing in visible
    assert visible == set(ACTIVE_IDS) | {own_hidden_listing}


def test_an_admin_sees_every_listing_in_every_status(client):
    response = client.get(PROPERTIES, headers=bearer(client, ADMIN_EMAIL))

    assert ids_of(response) == set(ACTIVE_IDS) | {
        DRAFT_OF_AGENT3,
        SOLD_OF_AGENT2,
        UNDER_OFFER_OF_AGENT1,
    }


def test_listing_requires_authentication(client):
    assert client.get(PROPERTIES).status_code == 401


# ------------------------------------------------- filters narrow, never widen, scope


def test_a_client_filtering_for_drafts_gets_an_empty_page_not_drafts(client):
    """The filter intersects the role scope. Not an error either — an empty list is the
    honest answer, and a 4xx here would itself be an existence oracle."""
    response = client.get(
        PROPERTIES, params={"status": "draft"}, headers=bearer(client, CLIENT_EMAIL)
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"] == []
    assert response.json()["total"] == 0


def test_an_agent_filtering_by_another_agents_id_never_widens_past_active(client):
    """``agent_id=<someone else>`` yields their *active* listings only — the ones the
    caller could already see — never that agent's draft."""
    response = client.get(
        PROPERTIES,
        params={"agent_id": AGENT3},
        headers=bearer(client, AGENT1_EMAIL),
    )

    assert DRAFT_OF_AGENT3 not in ids_of(response)
    assert ACTIVE_OF_AGENT3 in ids_of(response)


def test_an_agent_filtering_for_drafts_sees_only_their_own(client):
    for email, expected in ((AGENT3_EMAIL, {DRAFT_OF_AGENT3}), (AGENT1_EMAIL, set())):
        response = client.get(
            PROPERTIES, params={"status": "draft"}, headers=bearer(client, email)
        )
        assert ids_of(response) == expected, email


def test_combining_a_status_and_an_agent_filter_still_cannot_widen_scope(client):
    response = client.get(
        PROPERTIES,
        params={"status": "draft", "agent_id": AGENT3},
        headers=bearer(client, CLIENT_EMAIL),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_repeated_status_params_mean_or_within_the_filter(client):
    response = client.get(
        PROPERTIES,
        params=[("status", "draft"), ("status", "sold")],
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert ids_of(response) == {DRAFT_OF_AGENT3, SOLD_OF_AGENT2}


def test_free_text_price_and_bedroom_filters_are_anded(client):
    response = client.get(
        PROPERTIES,
        params={"listing_type": "rent", "min_price": 10_000_000, "bedrooms": 2},
        headers=bearer(client, ADMIN_EMAIL),
    )

    for item in response.json()["results"]:
        assert item["listing_type"] == "rent"
        assert item["price"] >= 10_000_000
        assert item["bedrooms"] >= 2


def test_an_inverted_price_range_is_rejected(client):
    response = client.get(
        PROPERTIES,
        params={"min_price": 9_000_000, "max_price": 1_000_000},
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422
    assert code_of(response) == "invalid_price_range"


# ----------------------------------------------------------------------- pagination


def test_page_size_above_the_ceiling_is_rejected_rather_than_clamped(client, settings):
    response = client.get(
        PROPERTIES,
        params={"page_size": settings.max_page_size + 1},
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422
    assert code_of(response) == "page_size_too_large"


def test_an_unknown_sort_field_is_rejected(client):
    response = client.get(
        PROPERTIES, params={"sort": "nonsense"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422
    assert code_of(response) == "invalid_sort_field"


def test_an_unknown_descending_sort_field_is_rejected_too(client):
    """The ``-`` prefix is stripped before the whitelist check, so ``-nonsense`` must
    not slip past it."""
    response = client.get(
        PROPERTIES, params={"sort": "-nonsense"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert code_of(response) == "invalid_sort_field"


def test_a_page_past_the_end_is_an_empty_page_with_the_real_total(client):
    response = client.get(
        PROPERTIES, params={"page": 99, "page_size": 5}, headers=bearer(client, ADMIN_EMAIL)
    )
    body = response.json()

    assert response.status_code == 200
    assert body["results"] == []
    assert body["total"] == 15
    assert body["total_pages"] == 3
    assert body["page"] == 99


def test_paging_walks_the_whole_scoped_set_without_gaps_or_repeats(client):
    headers = bearer(client, ADMIN_EMAIL)
    seen: list[str] = []
    for page in (1, 2, 3):
        response = client.get(
            PROPERTIES, params={"page": page, "page_size": 5, "sort": "title"}, headers=headers
        )
        seen.extend(item["id"] for item in response.json()["results"])

    assert len(seen) == 15
    assert len(set(seen)) == 15


def test_sorting_is_applied_in_the_requested_direction(client):
    headers = bearer(client, ADMIN_EMAIL)
    ascending = [
        item["price"] for item in client.get(PROPERTIES, params={"sort": "price"}, headers=headers).json()["results"]
    ]
    descending = [
        item["price"] for item in client.get(PROPERTIES, params={"sort": "-price"}, headers=headers).json()["results"]
    ]

    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)


# ------------------------------------------------------------------- write role gate


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", PROPERTIES), ("put", f"{PROPERTIES}/{ACTIVE_OF_AGENT1}"), ("delete", f"{PROPERTIES}/{ACTIVE_OF_AGENT1}")],
)
def test_a_client_may_not_write_properties_at_all(client, method, path):
    """Role-level denial is ``403 forbidden``: a ``client`` may not touch these
    endpoints, and that is not information worth hiding."""
    response = client.request(
        method, path, json=property_payload(), headers=bearer(client, CLIENT_EMAIL)
    )

    assert response.status_code == 403
    assert code_of(response) == "forbidden"


# ----------------------------------------------------------------------- create


def test_an_agent_creating_a_listing_always_owns_it(client):
    """``agent_id`` in the body is a request, not a commitment — an agent parking a
    listing on a colleague would also misattribute that listing's escalations."""
    response = client.post(
        PROPERTIES,
        json=property_payload(agent_id=AGENT2),
        headers=bearer(client, AGENT1_EMAIL),
    )

    assert response.status_code == 201, response.text
    assert response.json()["agent_id"] == AGENT1


def test_an_admin_may_assign_a_new_listing_to_someone_else(client):
    response = client.post(
        PROPERTIES, json=property_payload(agent_id=AGENT2), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.json()["agent_id"] == AGENT2


def test_a_created_listing_defaults_to_draft(client):
    """Publishing to every client by omission is the wrong direction to fail in."""
    response = client.post(
        PROPERTIES, json=property_payload(), headers=bearer(client, AGENT1_EMAIL)
    )

    assert response.json()["status"] == "draft"


def test_a_caller_cannot_choose_the_new_listings_id(client):
    response = client.post(
        PROPERTIES,
        json=property_payload(id="prop-001"),
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 201
    assert response.json()["id"] != "prop-001"


@pytest.mark.parametrize("method", ["post", "put"])
def test_an_unknown_agent_id_is_a_client_error_not_a_500(client, method):
    """`properties.agent_id` is a foreign key, so an unchecked id fails at the write and
    surfaces a raw `IntegrityError`. A bad body field is a 4xx, never a 500."""
    path = PROPERTIES if method == "post" else f"{PROPERTIES}/{ACTIVE_OF_AGENT1}"

    response = client.request(
        method,
        path,
        json=property_payload(agent_id="u-no-such-agent"),
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_agent_id"


@pytest.mark.parametrize("method", ["post", "put"])
def test_a_listing_may_not_be_assigned_to_a_client(client, method):
    """Same check, other half: a `client` cannot conduct a viewing, so a listing they
    own would be unbookable."""
    path = PROPERTIES if method == "post" else f"{PROPERTIES}/{ACTIVE_OF_AGENT1}"

    response = client.request(
        method,
        path,
        json=property_payload(agent_id=CLIENT),
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_agent_id"


def test_an_invalid_create_body_is_a_field_level_422(client):
    response = client.post(
        PROPERTIES, json=property_payload(price=-1), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422


# ------------------------------------------------------- write scope: the 404 posture


def test_an_agent_may_replace_their_own_listing(client):
    response = client.put(
        f"{PROPERTIES}/{ACTIVE_OF_AGENT1}",
        json=property_payload(title="Renamed", status="active"),
        headers=bearer(client, AGENT1_EMAIL),
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"


def test_an_agent_writing_another_agents_visible_listing_is_a_404(client):
    """The asymmetry that matters: ``prop-004`` is ``active``, so ``agent-1`` can read
    it in the same session — the write still answers ``404``, identically to a listing
    they cannot see at all."""
    headers = bearer(client, AGENT1_EMAIL)
    assert ACTIVE_OF_AGENT3 in ids_of(client.get(PROPERTIES, headers=headers))

    response = client.put(
        f"{PROPERTIES}/{ACTIVE_OF_AGENT3}", json=property_payload(), headers=headers
    )

    assert response.status_code == 404
    assert code_of(response) == "property_not_found"


@pytest.mark.parametrize(
    "property_id", [ACTIVE_OF_AGENT3, DRAFT_OF_AGENT3, "prop-does-not-exist"]
)
def test_visible_invisible_and_nonexistent_listings_are_one_answer_on_write(
    client, property_id
):
    """Readable-but-not-owned, invisible, and nonexistent must be indistinguishable, or
    the write path becomes the enumeration oracle the read path closed."""
    headers = bearer(client, AGENT1_EMAIL)

    put = client.put(f"{PROPERTIES}/{property_id}", json=property_payload(), headers=headers)
    delete = client.delete(f"{PROPERTIES}/{property_id}", headers=headers)

    assert (put.status_code, code_of(put)) == (404, "property_not_found")
    assert (delete.status_code, code_of(delete)) == (404, "property_not_found")


def test_an_out_of_scope_write_changes_nothing(client):
    """The 404 is not a rendering of a write that already happened."""
    client.put(
        f"{PROPERTIES}/{ACTIVE_OF_AGENT3}",
        json=property_payload(title="Hijacked"),
        headers=bearer(client, AGENT1_EMAIL),
    )

    with Session(client.app.state.engine) as session:
        assert session.get(Property, ACTIVE_OF_AGENT3).title != "Hijacked"


def test_an_admin_may_replace_any_listing(client):
    response = client.put(
        f"{PROPERTIES}/{DRAFT_OF_AGENT3}",
        json=property_payload(title="Admin edit", agent_id=AGENT3, status="draft"),
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Admin edit"


def test_an_agent_replacing_their_own_listing_cannot_hand_it_to_someone_else(client):
    response = client.put(
        f"{PROPERTIES}/{ACTIVE_OF_AGENT1}",
        json=property_payload(agent_id=AGENT2),
        headers=bearer(client, AGENT1_EMAIL),
    )

    assert response.json()["agent_id"] == AGENT1


# --------------------------------------------------------------------- deactivate


def test_deactivation_returns_the_listing_to_draft_rather_than_deleting_it(client):
    response = client.delete(
        f"{PROPERTIES}/{ACTIVE_OF_AGENT1}", headers=bearer(client, AGENT1_EMAIL)
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"
    with Session(client.app.state.engine) as session:
        assert session.get(Property, ACTIVE_OF_AGENT1) is not None


def test_a_deactivated_listing_leaves_a_clients_visible_set(client):
    client.delete(f"{PROPERTIES}/{ACTIVE_OF_AGENT1}", headers=bearer(client, AGENT1_EMAIL))

    visible = ids_of(client.get(PROPERTIES, headers=bearer(client, CLIENT_EMAIL)))

    assert ACTIVE_OF_AGENT1 not in visible


def test_deactivation_is_idempotent(client):
    headers = bearer(client, AGENT1_EMAIL)
    first = client.delete(f"{PROPERTIES}/{ACTIVE_OF_AGENT1}", headers=headers)
    second = client.delete(f"{PROPERTIES}/{ACTIVE_OF_AGENT1}", headers=headers)

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "draft"


# ---------------------------------------------------------------------------- CSRF


def test_a_cookie_write_without_the_csrf_header_is_rejected(client):
    cookie_login(client, AGENT1_EMAIL)

    response = client.post(PROPERTIES, json=property_payload())

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_missing"


def test_a_cookie_write_with_the_wrong_csrf_token_is_rejected(client):
    cookie_login(client, AGENT1_EMAIL)

    response = client.post(
        PROPERTIES, json=property_payload(), headers={"X-CSRF-Token": "not-the-token"}
    )

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


@pytest.mark.parametrize("method", ["put", "delete"])
def test_every_cookie_write_method_is_covered_not_just_post(client, method):
    """The guard is declared once at router level; this asserts it actually reaches the
    methods that were not spot-checked."""
    cookie_login(client, AGENT1_EMAIL)

    response = client.request(
        method, f"{PROPERTIES}/{ACTIVE_OF_AGENT1}", json=property_payload()
    )

    assert code_of(response) == "csrf_token_missing"


def test_a_cookie_read_needs_no_csrf_header(client):
    cookie_login(client, CLIENT_EMAIL)

    assert client.get(PROPERTIES).status_code == 200


def test_a_cookie_write_with_the_right_csrf_token_succeeds(client):
    csrf = cookie_login(client, AGENT1_EMAIL)

    response = client.post(
        PROPERTIES, json=property_payload(), headers={"X-CSRF-Token": csrf}
    )

    assert response.status_code == 201, response.text


def test_a_bearer_write_needs_no_csrf_header(client):
    """A Bearer caller has no ambient credential to forge, so CSRF does not apply."""
    response = client.post(
        PROPERTIES, json=property_payload(), headers=bearer(client, AGENT1_EMAIL)
    )

    assert response.status_code == 201, response.text
