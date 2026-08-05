"""`POST /auth/login`, `POST /auth/logout`, `GET /auth/me` against the README contract."""

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.security import create_access_token, hash_password, issue_csrf_token
from app.main import create_app
from app.models import User

from .conftest import SEED_PASSWORD, make_db_settings

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"

ADMIN_EMAIL = "rina.admin@evdekimi.test"
AGENT_EMAIL = "siti.agent@evdekimi.test"
CLIENT_EMAIL = "andi.client@evdekimi.test"
DISABLED_EMAIL = "disabled@evdekimi.test"
DISABLED_PASSWORD = "DisabledPass1!"


@pytest.fixture
def settings(tmp_path):
    return make_db_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def disabled_user(client):
    with Session(client.app.state.engine) as session:
        session.add(
            User(
                id="u-disabled-1",
                name="Disabled Person",
                email=DISABLED_EMAIL,
                role="agent",
                hashed_password=hash_password(DISABLED_PASSWORD),
                status="disabled",
            )
        )
        session.commit()
    return DISABLED_EMAIL


def login(client, email=ADMIN_EMAIL, password=SEED_PASSWORD, **extra):
    return client.post(LOGIN, json={"email": email, "password": password, **extra})


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def csrf_claim_of(settings, token) -> str | None:
    return jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    ).get("csrf")


# --- login: api mode ----------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "role", "user_id"),
    [
        (ADMIN_EMAIL, "admin", "u-admin-1"),
        (AGENT_EMAIL, "agent", "u-agent-1"),
        (CLIENT_EMAIL, "client", "u-client-1"),
    ],
)
def test_api_login_succeeds_for_every_role(client, settings, email, role, user_id):
    response = login(client, email)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["csrf_token"] is None
    assert body["expires_in"] == settings.jwt_expire_minutes * 60
    assert body["user"] == {
        "id": user_id,
        "name": body["user"]["name"],
        "email": email,
        "role": role,
    }


def test_api_login_is_the_default_client_type(client):
    """`client_type` is optional and defaults to `api` per the contract."""
    response = client.post(LOGIN, json={"email": ADMIN_EMAIL, "password": SEED_PASSWORD})

    assert response.json()["access_token"]
    assert "set-cookie" not in response.headers


def test_api_login_sets_no_cookie(client):
    response = login(client, client_type="api")

    assert "set-cookie" not in response.headers
    assert response.cookies.get("session") is None


def test_api_login_token_carries_no_csrf_claim(client, settings):
    token = login(client, client_type="api").json()["access_token"]

    assert csrf_claim_of(settings, token) is None


def test_login_response_never_exposes_the_password_hash(client):
    body = login(client).text

    assert "hashed_password" not in body
    assert "$2b$" not in body


def test_email_matching_is_case_and_whitespace_insensitive(client):
    response = login(client, email=f"  {ADMIN_EMAIL.upper()}  ")

    assert response.status_code == 200


# --- login: no user-enumeration oracle ----------------------------------------


def test_wrong_password_and_unknown_email_are_byte_identical(client):
    """The whole point of the identical message: neither status, code, nor body may
    differ, or the endpoint becomes a user-enumeration oracle."""
    wrong_password = login(client, ADMIN_EMAIL, "NotThePassword1!")
    unknown_email = login(client, "nobody@evdekimi.test", "NotThePassword1!")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.content == unknown_email.content
    assert code_of(wrong_password) == "invalid_credentials"


def test_malformed_email_is_a_401_not_a_422(client):
    """Schema-level email validation would itself be an oracle: "that address could
    not exist" is different information from "that address is not registered"."""
    response = login(client, "not-an-email-at-all")

    assert response.status_code == 401
    assert code_of(response) == "invalid_credentials"


def test_malformed_email_response_is_identical_to_an_unknown_one(client):
    assert login(client, "not-an-email").content == login(client, "ghost@evdekimi.test").content


def test_over_length_password_is_a_clean_401(client):
    """bcrypt rejects >72 bytes; that must surface as a failed login, not a 500."""
    response = login(client, ADMIN_EMAIL, "a" * 1000)

    assert response.status_code == 401
    assert code_of(response) == "invalid_credentials"


# --- login: disabled accounts -------------------------------------------------


def test_disabled_account_with_a_wrong_password_is_invalid_credentials(
    client, disabled_user
):
    """Status is checked only after the password verifies: rejecting earlier would let
    anyone discover that an address exists and is disabled without holding its
    password."""
    response = login(client, DISABLED_EMAIL, "NotThePassword1!")

    assert response.status_code == 401
    assert code_of(response) == "invalid_credentials"
    assert response.content == login(client, "ghost@evdekimi.test", "NotThePassword1!").content


def test_disabled_account_with_the_correct_password_is_403_account_disabled(
    client, disabled_user
):
    response = login(client, DISABLED_EMAIL, DISABLED_PASSWORD)

    assert response.status_code == 403
    assert code_of(response) == "account_disabled"


def test_disabled_account_receives_no_credential(client, disabled_user):
    response = login(client, DISABLED_EMAIL, DISABLED_PASSWORD, client_type="browser")

    assert "set-cookie" not in response.headers
    assert "access_token" not in response.text


# --- login: browser mode ------------------------------------------------------


def test_browser_login_sets_an_httponly_cookie_and_withholds_the_token(client):
    response = login(client, client_type="browser")

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] is None, "a raw Bearer token in JS memory defeats httpOnly"
    assert body["token_type"] is None
    assert body["csrf_token"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/" in set_cookie
    assert f"max-age={body['expires_in']}" in set_cookie


def test_browser_login_csrf_token_matches_the_claim_inside_the_cookie(client, settings):
    """Double-submit is only meaningful if the two values are actually bound."""
    response = login(client, client_type="browser")

    cookie_jwt = client.cookies[settings.session_cookie_name]
    assert csrf_claim_of(settings, cookie_jwt) == response.json()["csrf_token"]


def test_browser_login_csrf_tokens_are_unique_per_session(client):
    first = login(client, client_type="browser").json()["csrf_token"]
    second = login(client, client_type="browser").json()["csrf_token"]

    assert first != second


def test_cookie_is_marked_secure_when_configured(tmp_path):
    settings = make_db_settings(tmp_path, session_cookie_secure=True)
    with TestClient(create_app(settings)) as secure_client:
        response = login(secure_client, client_type="browser")

    assert "secure" in response.headers["set-cookie"].lower()


def test_the_cookie_name_is_configurable_end_to_end(tmp_path):
    """Set and read must both come from settings, with no hardcoded "session"."""
    settings = make_db_settings(tmp_path, session_cookie_name="rea_session")
    with TestClient(create_app(settings)) as renamed:
        response = login(renamed, client_type="browser")
        csrf = response.json()["csrf_token"]

        assert "rea_session=" in response.headers["set-cookie"]
        assert renamed.get(ME).json()["session"]["csrf_token"] == csrf
        assert renamed.post(LOGOUT, headers={"X-CSRF-Token": csrf}).status_code == 204


def test_browser_login_is_open_to_client_role_too(client):
    """Role-gating the dashboard is the SPA's concern; the backend gates each endpoint."""
    response = login(client, CLIENT_EMAIL, client_type="browser")

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "client"


def test_browser_cookie_authenticates_a_subsequent_request(client):
    login(client, client_type="browser")

    assert client.get(ME).status_code == 200


@pytest.mark.parametrize("client_type", ["", "cookie", "mobile", 5])
def test_unknown_client_type_is_rejected(client, client_type):
    response = login(client, client_type=client_type)

    assert response.status_code == 422


# --- login: request validation ------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": ADMIN_EMAIL},
        {"password": SEED_PASSWORD},
        {"email": "", "password": SEED_PASSWORD},
        {"email": ADMIN_EMAIL, "password": ""},
        {"email": None, "password": SEED_PASSWORD},
    ],
    ids=["empty", "no-password", "no-email", "blank-email", "blank-password", "null-email"],
)
def test_malformed_login_body_is_422(client, payload):
    assert client.post(LOGIN, json=payload).status_code == 422


def test_login_needs_no_authentication(client):
    """Regression guard: login must never end up behind the session dependency."""
    assert login(client).status_code == 200


# --- GET /auth/me -------------------------------------------------------------


def test_me_over_a_cookie_session_returns_identity_and_the_csrf_token(client):
    """Survives a page reload: the SPA's in-memory CSRF token is gone but the
    httpOnly cookie is not, so /me has to hand the token back."""
    issued = login(client, AGENT_EMAIL, client_type="browser").json()["csrf_token"]

    response = client.get(ME)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "u-agent-1"
    assert body["email"] == AGENT_EMAIL
    assert body["role"] == "agent"
    assert body["status"] == "active"
    assert body["session"]["auth_method"] == "cookie"
    assert body["session"]["csrf_token"] == issued
    assert body["session"]["expires_at"]


def test_me_over_a_bearer_session_has_no_csrf_token(client):
    token = login(client, AGENT_EMAIL).json()["access_token"]

    body = client.get(ME, headers={"Authorization": f"Bearer {token}"}).json()

    assert body["session"]["auth_method"] == "bearer"
    assert body["session"]["csrf_token"] is None


def test_me_returned_csrf_token_still_authorizes_a_write(client):
    login(client, client_type="browser")

    recovered = client.get(ME).json()["session"]["csrf_token"]

    assert client.post(LOGOUT, headers={"X-CSRF-Token": recovered}).status_code == 204


def test_me_never_exposes_the_password_hash(client):
    login(client, client_type="browser")

    body = client.get(ME).text

    assert "hashed_password" not in body and "$2b$" not in body


def test_me_without_a_credential_is_401(client):
    response = client.get(ME)

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_me_with_an_invalid_token_is_401(client):
    response = client.get(ME, headers={"Authorization": "Bearer garbage"})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_me_is_401_session_revoked_after_the_account_is_disabled(client):
    token = login(client, AGENT_EMAIL).json()["access_token"]
    with Session(client.app.state.engine) as session:
        user = session.get(User, "u-agent-1")
        user.status = "disabled"
        session.add(user)
        session.commit()

    response = client.get(ME, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert code_of(response) == "session_revoked"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 10**19},
        {"exp": "9999999999"},
        {"exp": True},
        {"exp": None},
        {"exp": float("inf")},
        {"exp": float("nan")},
        {"exp": 9999999999, "nbf": float("inf")},
        {"exp": 9999999999, "iat": "yesterday"},
    ],
    ids=[
        "out-of-range",
        "string",
        "bool",
        "null",
        "inf",
        "nan",
        "hostile-nbf",
        "hostile-iat",
    ],
)
def test_hostile_temporal_claims_are_401_not_500(client, settings, claims):
    """DEFECT-1 regression at the contract surface: an unusable claim is a failed
    authentication, not a server fault. The unit-level coverage lives in
    test_security.py; this pins the status code the SPA actually branches on, since a
    500 would leave it showing an error page instead of redirecting to /login.
    """
    token = jwt.encode(
        {"sub": "u-admin-1", "role": "admin", **claims},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(ME, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_me_is_scoped_to_the_caller_and_ignores_a_user_id_query(client):
    """There is no ?user_id= variant; identity comes only from the validated token."""
    login(client, CLIENT_EMAIL, client_type="browser")

    body = client.get(f"{ME}?user_id=u-admin-1").json()

    assert body["id"] == "u-client-1"


def test_me_is_csrf_exempt_as_a_safe_method(client):
    login(client, client_type="browser")

    assert client.get(ME).status_code == 200


# --- POST /auth/logout --------------------------------------------------------


def test_logout_clears_the_cookie_with_a_valid_csrf_header(client, settings):
    csrf = login(client, client_type="browser").json()["csrf_token"]

    response = client.post(LOGOUT, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 204
    assert not response.content
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.cookies.get(settings.session_cookie_name) is None


def test_the_session_is_actually_dead_after_logout(client):
    csrf = login(client, client_type="browser").json()["csrf_token"]

    client.post(LOGOUT, headers={"X-CSRF-Token": csrf})

    assert client.get(ME).status_code == 401


def test_logout_without_a_csrf_header_is_403(client):
    """Beyond the literal spec: without this, a cross-site form could log the user out."""
    login(client, client_type="browser")

    response = client.post(LOGOUT)

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_missing"


def test_logout_with_a_wrong_csrf_header_is_403(client):
    login(client, client_type="browser")

    response = client.post(LOGOUT, headers={"X-CSRF-Token": issue_csrf_token()})

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


def test_a_refused_logout_leaves_the_session_alive(client):
    login(client, client_type="browser")

    client.post(LOGOUT)

    assert client.get(ME).status_code == 200


def test_logout_without_any_session_is_204(client):
    """Idempotent, so a double-click or a stale tab does not surface an error."""
    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert "set-cookie" not in response.headers


def test_double_logout_is_204(client):
    csrf = login(client, client_type="browser").json()["csrf_token"]
    client.post(LOGOUT, headers={"X-CSRF-Token": csrf})

    assert client.post(LOGOUT, headers={"X-CSRF-Token": csrf}).status_code == 204


def test_logout_with_an_expired_cookie_is_204_and_still_clears_it(client, tmp_path):
    stale = make_db_settings(tmp_path, jwt_expire_minutes=-1)
    token, _ = create_access_token(
        stale, user_id="u-admin-1", role="admin", csrf_token=issue_csrf_token()
    )
    client.cookies.set("session", token)

    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_bearer_logout_is_204_with_no_cookie_header(client):
    """MVP JWTs are stateless and are not denylisted (ruling A3)."""
    token = login(client).json()["access_token"]

    response = client.post(LOGOUT, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204
    assert "set-cookie" not in response.headers


def test_bearer_logout_needs_no_csrf_header(client):
    token = login(client).json()["access_token"]

    assert client.post(LOGOUT, headers={"Authorization": f"Bearer {token}"}).status_code == 204


@pytest.mark.parametrize("email", [ADMIN_EMAIL, AGENT_EMAIL, CLIENT_EMAIL])
def test_logout_is_available_to_every_role(client, email):
    csrf = login(client, email, client_type="browser").json()["csrf_token"]

    assert client.post(LOGOUT, headers={"X-CSRF-Token": csrf}).status_code == 204


# --- OpenAPI surface ----------------------------------------------------------


def test_auth_routes_are_documented_under_the_v1_prefix(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert {LOGIN, LOGOUT, ME} <= set(paths)
