"""``/api/v1/users`` — admin-only user administration.

Two properties carry the risk here. First, this is the one router whose denial is
``403 forbidden`` rather than the ``404`` used elsewhere, because a non-admin may not
touch it at all. Second, ``User`` carries ``hashed_password``, so every response is
checked against its **raw bytes** rather than against the declared response model — a
response model proves what was meant, not what was sent.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.security import verify_password
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import User

from .conftest import SEED_PASSWORD, make_db_settings
from .test_properties_api import bearer, code_of, cookie_login

USERS = "/api/v1/users"
LOGIN = "/api/v1/auth/login"

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT_EMAIL = "siti.agent@evdekimi.test"
CLIENT_EMAIL = "andi.client@evdekimi.test"

SEEDED_USER_COUNT = 8
BCRYPT_PREFIX = "$2b$"


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def user_payload(**overrides) -> dict:
    return {
        "name": "New Person",
        "email": "new.person@evdekimi.test",
        "password": "NewPassword1!",
        "role": "agent",
        **overrides,
    }


def assert_no_credential_material(response) -> None:
    """Against ``response.content``, not the parsed body: a leak added by a future
    field would still be bytes on the wire even if the model does not name it."""
    raw = response.content.decode()
    assert "hashed_password" not in raw
    assert "password" not in raw
    assert BCRYPT_PREFIX not in raw


# ------------------------------------------------------------------------- role gate


@pytest.mark.parametrize("email", [AGENT_EMAIL, CLIENT_EMAIL])
def test_a_non_admin_may_not_list_users(client, email):
    response = client.get(USERS, headers=bearer(client, email))

    assert response.status_code == 403
    assert code_of(response) == "forbidden"


@pytest.mark.parametrize("email", [AGENT_EMAIL, CLIENT_EMAIL])
def test_a_non_admin_may_not_create_users(client, email):
    response = client.post(USERS, json=user_payload(), headers=bearer(client, email))

    assert response.status_code == 403
    assert code_of(response) == "forbidden"


@pytest.mark.parametrize("email", [AGENT_EMAIL, CLIENT_EMAIL])
def test_a_non_admin_may_not_patch_users(client, email):
    response = client.patch(
        f"{USERS}/u-client-2", json={"status": "disabled"}, headers=bearer(client, email)
    )

    assert response.status_code == 403
    assert code_of(response) == "forbidden"


def test_an_agent_cannot_patch_even_their_own_record(client):
    """No self-service carve-out: the role gate is the whole authorization for this
    router, so an exception for "my own id" would be a second, weaker path into it."""
    response = client.patch(
        f"{USERS}/u-agent-1", json={"name": "Renamed"}, headers=bearer(client, AGENT_EMAIL)
    )

    assert response.status_code == 403


def test_the_router_requires_authentication(client):
    assert client.get(USERS).status_code == 401


def test_a_non_admin_cannot_escalate_themselves_to_admin(client):
    client.patch(
        f"{USERS}/u-client-1", json={"role": "admin"}, headers=bearer(client, CLIENT_EMAIL)
    )

    with Session(client.app.state.engine) as session:
        assert session.get(User, "u-client-1").role == "client"


# ------------------------------------------------------------ no credential material


def test_the_list_response_never_carries_credential_material(client):
    response = client.get(USERS, headers=bearer(client, ADMIN_EMAIL))

    assert response.status_code == 200, response.text
    assert response.json()["total"] == SEEDED_USER_COUNT
    assert_no_credential_material(response)


def test_the_create_response_never_echoes_the_password(client):
    response = client.post(USERS, json=user_payload(), headers=bearer(client, ADMIN_EMAIL))

    assert response.status_code == 201, response.text
    assert_no_credential_material(response)


def test_the_patch_response_never_echoes_a_password_reset(client):
    response = client.patch(
        f"{USERS}/u-client-2",
        json={"password": "RotatedPass1!"},
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 200, response.text
    assert_no_credential_material(response)


def test_a_user_response_carries_exactly_the_contracted_fields(client):
    response = client.get(USERS, headers=bearer(client, ADMIN_EMAIL))

    assert set(response.json()["results"][0]) == {
        "id",
        "name",
        "email",
        "role",
        "status",
        "created_at",
    }


# ------------------------------------------------------------------------ filtering


def test_the_role_filter_is_repeatable(client):
    response = client.get(
        USERS, params=[("role", "admin"), ("role", "agent")], headers=bearer(client, ADMIN_EMAIL)
    )

    assert {item["role"] for item in response.json()["results"]} == {"admin", "agent"}


def test_free_text_searches_name_and_email(client):
    headers = bearer(client, ADMIN_EMAIL)

    by_name = client.get(USERS, params={"q": "Rina"}, headers=headers)
    by_email = client.get(USERS, params={"q": "siti.agent"}, headers=headers)

    assert {item["id"] for item in by_name.json()["results"]} == {"u-admin-1"}
    assert {item["id"] for item in by_email.json()["results"]} == {"u-agent-1"}


def test_the_status_filter_narrows_to_disabled_users(client):
    headers = bearer(client, ADMIN_EMAIL)
    client.patch(f"{USERS}/u-client-4", json={"status": "disabled"}, headers=headers)

    response = client.get(USERS, params={"status": "disabled"}, headers=headers)

    assert {item["id"] for item in response.json()["results"]} == {"u-client-4"}


# ---------------------------------------------------------------------- pagination


def test_page_size_above_the_ceiling_is_rejected(client, settings):
    response = client.get(
        USERS,
        params={"page_size": settings.max_page_size + 1},
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert response.status_code == 422
    assert code_of(response) == "page_size_too_large"


def test_an_unknown_sort_field_is_rejected(client):
    response = client.get(
        USERS, params={"sort": "status"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422
    assert code_of(response) == "invalid_sort_field"


def test_a_page_past_the_end_is_empty_with_the_real_total(client):
    response = client.get(
        USERS, params={"page": 20, "page_size": 3}, headers=bearer(client, ADMIN_EMAIL)
    )
    body = response.json()

    assert response.status_code == 200
    assert body["results"] == []
    assert body["total"] == SEEDED_USER_COUNT
    assert body["total_pages"] == 3


def test_the_default_sort_is_by_name(client):
    response = client.get(USERS, headers=bearer(client, ADMIN_EMAIL))

    names = [item["name"] for item in response.json()["results"]]
    assert names == sorted(names)


# --------------------------------------------------------------------------- create


def test_a_created_user_can_log_in_with_the_supplied_password(client):
    """The end-to-end proof that the password was hashed rather than stored verbatim —
    and that email normalization matches how login looks an address up."""
    client.post(
        USERS,
        json=user_payload(email="  Mixed.Case@Evdekimi.Test  "),
        headers=bearer(client, ADMIN_EMAIL),
    )

    login = client.post(
        LOGIN, json={"email": "mixed.case@evdekimi.test", "password": "NewPassword1!"}
    )

    assert login.status_code == 200, login.text


def test_a_created_users_password_is_stored_hashed(client):
    created = client.post(
        USERS, json=user_payload(), headers=bearer(client, ADMIN_EMAIL)
    ).json()

    with Session(client.app.state.engine) as session:
        stored = session.get(User, created["id"]).hashed_password

    assert stored != "NewPassword1!"
    assert verify_password("NewPassword1!", stored)


def test_a_duplicate_email_is_rejected(client):
    response = client.post(
        USERS, json=user_payload(email=CLIENT_EMAIL), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 409
    assert code_of(response) == "email_already_exists"


def test_a_duplicate_email_differing_only_in_case_is_rejected(client):
    response = client.post(
        USERS,
        json=user_payload(email=CLIENT_EMAIL.upper()),
        headers=bearer(client, ADMIN_EMAIL),
    )

    assert code_of(response) == "email_already_exists"


def test_a_short_password_is_a_field_level_422(client):
    response = client.post(
        USERS, json=user_payload(password="short"), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422


def test_a_password_past_bcrypts_limit_is_rejected_before_it_reaches_the_hasher(client):
    """72 bytes is bcrypt's truncation point; a 500 from the hasher would be the
    alternative."""
    response = client.post(
        USERS, json=user_payload(password="x" * 73), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422


def test_an_unknown_role_is_a_field_level_422(client):
    response = client.post(
        USERS, json=user_payload(role="superuser"), headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------- patch


def test_an_omitted_field_is_left_untouched(client):
    headers = bearer(client, ADMIN_EMAIL)
    before = client.get(USERS, params={"q": "Maria"}, headers=headers).json()["results"][0]

    response = client.patch(f"{USERS}/u-client-2", json={"status": "disabled"}, headers=headers)
    after = response.json()

    assert after["status"] == "disabled"
    assert after["name"] == before["name"]
    assert after["email"] == before["email"]
    assert after["role"] == before["role"]


def test_disabling_a_user_blocks_their_next_login(client):
    """The deactivation path, since there is no DELETE — deleting would orphan their
    bookings."""
    client.patch(
        f"{USERS}/u-client-2", json={"status": "disabled"}, headers=bearer(client, ADMIN_EMAIL)
    )

    login = client.post(LOGIN, json={"email": "maria.client@evdekimi.test", "password": SEED_PASSWORD})

    assert login.status_code == 403
    assert code_of(login) == "account_disabled"


def test_a_password_reset_takes_effect(client):
    client.patch(
        f"{USERS}/u-client-2",
        json={"password": "RotatedPass1!"},
        headers=bearer(client, ADMIN_EMAIL),
    )

    login = client.post(
        LOGIN, json={"email": "maria.client@evdekimi.test", "password": "RotatedPass1!"}
    )

    assert login.status_code == 200, login.text


def test_patching_an_email_onto_another_users_address_is_rejected(client):
    response = client.patch(
        f"{USERS}/u-client-2", json={"email": CLIENT_EMAIL}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 409
    assert code_of(response) == "email_already_exists"


def test_patching_a_user_with_their_own_email_is_not_a_conflict(client):
    response = client.patch(
        f"{USERS}/u-client-1", json={"email": CLIENT_EMAIL}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 200, response.text


def test_patching_an_unknown_user_is_a_404(client):
    response = client.patch(
        f"{USERS}/u-nobody", json={"name": "X"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.status_code == 404
    assert code_of(response) == "user_not_found"


def test_a_role_change_is_applied(client):
    response = client.patch(
        f"{USERS}/u-client-3", json={"role": "agent"}, headers=bearer(client, ADMIN_EMAIL)
    )

    assert response.json()["role"] == "agent"


# ---------------------------------------------------------------------------- CSRF


def test_a_cookie_create_without_the_csrf_header_is_rejected(client):
    cookie_login(client, ADMIN_EMAIL)

    response = client.post(USERS, json=user_payload())

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_missing"


def test_a_cookie_patch_without_the_csrf_header_is_rejected(client):
    cookie_login(client, ADMIN_EMAIL)

    response = client.patch(f"{USERS}/u-client-2", json={"status": "disabled"})

    assert code_of(response) == "csrf_token_missing"


def test_a_cookie_write_with_the_wrong_csrf_token_is_rejected(client):
    cookie_login(client, ADMIN_EMAIL)

    response = client.post(
        USERS, json=user_payload(), headers={"X-CSRF-Token": "wrong"}
    )

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


def test_a_cookie_read_needs_no_csrf_header(client):
    cookie_login(client, ADMIN_EMAIL)

    assert client.get(USERS).status_code == 200


def test_a_cookie_write_with_the_right_csrf_token_succeeds(client):
    csrf = cookie_login(client, ADMIN_EMAIL)

    response = client.post(USERS, json=user_payload(), headers={"X-CSRF-Token": csrf})

    assert response.status_code == 201, response.text


def test_a_bearer_write_needs_no_csrf_header(client):
    response = client.post(USERS, json=user_payload(), headers=bearer(client, ADMIN_EMAIL))

    assert response.status_code == 201, response.text


def test_csrf_is_checked_before_the_role_gate(client):
    """A non-admin cookie caller gets the CSRF answer, not ``forbidden`` — the router's
    guard runs first, so neither ordering leaks anything the other hides."""
    cookie_login(client, CLIENT_EMAIL)

    response = client.post(USERS, json=user_payload())

    assert code_of(response) == "csrf_token_missing"
