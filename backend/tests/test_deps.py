"""Session resolution, role gating, and CSRF enforcement in app/api/deps.py.

Exercised through real routes on a real app so the dependency ordering under test
is the same ordering FastAPI will use in production — asserting on the functions
in isolation would not catch an ordering regression.
"""

import fakeredis
import jwt
import pytest
import redis
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import SessionContextDep, enforce_csrf, require_role
from app.core.revocation import RedisTokenDenylist
from app.core.security import create_access_token, decode_access_token, issue_csrf_token
from app.db.session import build_engine, create_tables
from app.main import create_app
from app.models import User

from .conftest import make_db_settings

ADMIN = ("u-admin-1", "admin")
AGENT = ("u-agent-1", "agent")
CLIENT = ("u-client-1", "client")
ALL_ROLES = [ADMIN, AGENT, CLIENT]
WRITE_METHODS = ["post", "put", "patch", "delete"]


def _register_probe_routes(app: FastAPI) -> None:
    @app.get("/_probe/whoami")
    def whoami(context: SessionContextDep) -> dict:
        return {
            "user_id": context.user.id,
            "role": context.user.role,
            "auth_method": context.auth_method,
            "csrf_token": context.csrf_token,
        }

    @app.api_route(
        "/_probe/write",
        methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
        dependencies=[Depends(enforce_csrf)],
    )
    def write() -> dict:
        return {"ok": True}

    @app.get("/_probe/admin-only")
    def admin_only(user: User = Depends(require_role("admin"))) -> dict:
        return {"user_id": user.id}

    @app.get("/_probe/staff")
    def staff(user: User = Depends(require_role("admin", "agent"))) -> dict:
        return {"user_id": user.id}


@pytest.fixture
def settings(tmp_path):
    settings = make_db_settings(tmp_path)
    # Schema creation is `alembic upgrade head`'s job (see infra/backend/
    # Dockerfile), not app/main.py's lifespan — this mimics that step having
    # already run before the app starts, using create_all() directly rather
    # than real Alembic (slow, and tests Alembic rather than app code).
    create_tables(build_engine(settings))
    return settings


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    """fakeredis rather than a real Redis instance, which is not available in this
    environment (see backend/pyproject.toml's dev group note). Exposed separately from
    `denylist` so a test can manipulate the raw keys behind it."""
    return fakeredis.FakeRedis()


@pytest.fixture
def denylist(redis_client) -> RedisTokenDenylist:
    return RedisTokenDenylist(redis_client)


@pytest.fixture
def client(settings, denylist):
    app = create_app(settings, token_denylist=denylist)
    _register_probe_routes(app)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def disabled_user(client):
    """No seeded account is disabled, so session_revoked needs one made here."""
    with Session(client.app.state.engine) as session:
        session.add(
            User(
                id="u-disabled-1",
                name="Disabled Person",
                email="disabled@evdekimi.test",
                role="agent",
                hashed_password="$2b$12$notarealhash",
                status="disabled",
            )
        )
        session.commit()
    return "u-disabled-1", "agent"


def bearer(settings, identity, **kwargs) -> dict:
    user_id, role = identity
    token, _ = create_access_token(settings, user_id=user_id, role=role, **kwargs)
    return {"Authorization": f"Bearer {token}"}


def cookie_session(client, settings, identity, csrf_token=None, **kwargs):
    """Install a cookie session on the client; returns its CSRF token."""
    user_id, role = identity
    csrf_token = issue_csrf_token() if csrf_token is None else csrf_token
    token, _ = create_access_token(
        settings, user_id=user_id, role=role, csrf_token=csrf_token, **kwargs
    )
    client.cookies.set(settings.session_cookie_name, token)
    return csrf_token


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- credential resolution ----------------------------------------------------


@pytest.mark.parametrize("identity", ALL_ROLES, ids=lambda i: i[1])
def test_bearer_token_resolves_the_user(client, settings, identity):
    response = client.get("/_probe/whoami", headers=bearer(settings, identity))

    assert response.status_code == 200
    assert response.json()["user_id"] == identity[0]
    assert response.json()["auth_method"] == "bearer"


@pytest.mark.parametrize("identity", ALL_ROLES, ids=lambda i: i[1])
def test_cookie_session_resolves_the_user(client, settings, identity):
    cookie_session(client, settings, identity)

    response = client.get("/_probe/whoami")

    assert response.status_code == 200
    assert response.json()["user_id"] == identity[0]
    assert response.json()["auth_method"] == "cookie"


def test_bearer_wins_when_both_credentials_are_present(client, settings):
    """Documented precedence: an explicit header is a deliberate credential choice,
    the cookie may just be ambient."""
    cookie_session(client, settings, CLIENT)

    response = client.get("/_probe/whoami", headers=bearer(settings, ADMIN))

    assert response.json()["user_id"] == "u-admin-1"
    assert response.json()["auth_method"] == "bearer"


def test_empty_bearer_header_falls_back_to_the_cookie(client, settings):
    cookie_session(client, settings, CLIENT)

    response = client.get("/_probe/whoami", headers={"Authorization": "Bearer    "})

    assert response.status_code == 200
    assert response.json()["auth_method"] == "cookie"


def test_bearer_scheme_is_matched_case_insensitively(client, settings):
    token = bearer(settings, ADMIN)["Authorization"].split(" ", 1)[1]

    response = client.get("/_probe/whoami", headers={"Authorization": f"bEaReR {token}"})

    assert response.status_code == 200


def test_bearer_context_never_carries_a_csrf_token(client, settings):
    """Even if a csrf claim rides along, it must not be surfaced for a Bearer call."""
    response = client.get(
        "/_probe/whoami", headers=bearer(settings, ADMIN, csrf_token="leaked-token")
    )

    assert response.json()["csrf_token"] is None


def test_no_credential_is_401_not_authenticated(client):
    response = client.get("/_probe/whoami")

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer garbage"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer a.b.c"},
    ],
)
def test_unusable_authorization_headers_are_401(client, header):
    response = client.get("/_probe/whoami", headers=header)

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_tampered_token_is_401(client, settings):
    token = bearer(settings, ADMIN)["Authorization"].split(" ", 1)[1]
    head, payload, signature = token.split(".")

    response = client.get(
        "/_probe/whoami", headers={"Authorization": f"Bearer {head}.{payload}.{signature[:-2]}xx"}
    )

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_token_signed_with_another_secret_is_401(client, tmp_path):
    forged = make_db_settings(tmp_path, jwt_secret_key="attacker-secret")

    response = client.get("/_probe/whoami", headers=bearer(forged, ADMIN))

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_expired_token_is_401(client, tmp_path):
    stale = make_db_settings(tmp_path, jwt_expire_minutes=-1)

    response = client.get("/_probe/whoami", headers=bearer(stale, ADMIN))

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_token_for_an_unknown_user_is_401_session_revoked(client, settings):
    response = client.get("/_probe/whoami", headers=bearer(settings, ("u-ghost", "admin")))

    assert response.status_code == 401
    assert code_of(response) == "session_revoked"


def test_token_for_a_disabled_user_is_401_session_revoked(client, settings, disabled_user):
    """A disabled account must not keep a working UI until its JWT expires."""
    response = client.get("/_probe/whoami", headers=bearer(settings, disabled_user))

    assert response.status_code == 401
    assert code_of(response) == "session_revoked"


def test_role_is_read_from_the_database_not_the_token(client, settings):
    """A forged role claim must not escalate: the row is the source of truth."""
    response = client.get("/_probe/whoami", headers=bearer(settings, ("u-client-1", "admin")))

    assert response.json()["role"] == "client"


# --- token revocation denylist (app/core/revocation.py) -----------------------


def test_revoked_bearer_token_is_401_not_authenticated(client, settings, denylist):
    """A revoked token must read exactly like an expired/invalid one -- not a
    distinguishable "this token was explicitly revoked" state."""
    token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_revoked_cookie_session_is_401_not_authenticated(client, settings, denylist):
    csrf = cookie_session(client, settings, ADMIN)
    token = client.cookies[settings.session_cookie_name]
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_revoking_one_token_does_not_affect_a_different_token_for_the_same_user(
    client, settings, denylist
):
    revoked_token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    other_token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    denylist.revoke(decode_access_token(settings, revoked_token).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {other_token}"})

    assert response.status_code == 200


def test_a_token_without_a_jti_authenticates_normally(client, settings):
    """A token issued before the revocation feature existed has no jti and simply
    cannot be checked against the denylist -- it authenticates as before rather than
    being rejected outright (see TokenClaims.jti)."""
    token = jwt.encode(
        {"sub": "u-admin-1", "role": "admin", "exp": 9999999999},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


class _AlwaysBrokenClient:
    """Stands in for a `redis.Redis` whose connection is down."""

    def exists(self, *_args, **_kwargs):
        raise redis.ConnectionError("simulated outage")

    def set(self, *_args, **_kwargs):
        raise redis.ConnectionError("simulated outage")


@pytest.mark.parametrize("identity", ALL_ROLES, ids=lambda i: i[1])
def test_a_revoked_token_is_rejected_for_every_role(client, settings, denylist, identity):
    """No role is exempt from the denylist check -- an admin's revoked token is as dead
    as a client's."""
    token, _ = create_access_token(settings, user_id=identity[0], role=identity[1])
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_revocation_is_checked_before_role_gating(client, settings, denylist):
    """Ordering matters: a revoked admin token must read as 401 not_authenticated, never
    reach require_role, and never come back 403 -- a 403 would confirm to the holder
    that the token still resolves to a real, authenticated identity."""
    token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.get("/_probe/admin-only", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_revocation_is_checked_before_csrf(client, settings, denylist):
    """Same contract as the module docstring's 401-before-403 ordering: a revoked cookie
    session with a perfectly valid CSRF header is still 401, not a confusing 403."""
    csrf = cookie_session(client, settings, ADMIN)
    token = client.cookies[settings.session_cookie_name]
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.post("/_probe/write", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_revocation_is_checked_before_the_user_lookup(client, settings, denylist):
    """A revoked token for a user that does not exist must still be not_authenticated,
    not session_revoked -- otherwise the two distinct 401 codes would leak whether the
    subject of a revoked token is a real account."""
    token, _ = create_access_token(settings, user_id="u-ghost", role="admin")
    denylist.revoke(decode_access_token(settings, token).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"})

    assert code_of(response) == "not_authenticated"


def test_revoking_one_users_token_leaves_another_users_token_alive(client, settings, denylist):
    """Cross-user isolation at the dependency layer."""
    revoked, _ = create_access_token(settings, user_id="u-client-1", role="client")
    untouched, _ = create_access_token(settings, user_id="u-agent-1", role="agent")
    denylist.revoke(decode_access_token(settings, revoked).jti, ttl_seconds=3600)

    response = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {untouched}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "u-agent-1"


def test_an_expiring_denylist_entry_stops_blocking_once_it_lapses(
    client, settings, denylist, redis_client
):
    """The entry's TTL is what bounds denylist growth; when it lapses the token would be
    expired anyway. Asserted via the entry's own expiry rather than by waiting."""
    token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    jti = decode_access_token(settings, token).jti
    denylist.revoke(jti, ttl_seconds=3600)
    assert client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    redis_client.delete(f"revoked_jti:{jti}".encode())  # simulate the TTL lapsing

    assert client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_an_unreachable_denylist_does_not_block_authentication(settings):
    """The fail-open contract at the layer that actually matters: an outage must
    never turn into every authenticated request being rejected."""
    app = create_app(settings, token_denylist=RedisTokenDenylist(_AlwaysBrokenClient()))
    _register_probe_routes(app)
    token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")

    with TestClient(app) as broken_client:
        response = broken_client.get(
            "/_probe/whoami", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200


# --- require_role -------------------------------------------------------------


@pytest.mark.parametrize(
    ("identity", "expected"),
    [(ADMIN, 200), (AGENT, 403), (CLIENT, 403)],
    ids=["admin", "agent", "client"],
)
def test_admin_only_route_rbac_matrix(client, settings, identity, expected):
    response = client.get("/_probe/admin-only", headers=bearer(settings, identity))

    assert response.status_code == expected
    if expected == 403:
        assert code_of(response) == "forbidden"


@pytest.mark.parametrize(
    ("identity", "expected"),
    [(ADMIN, 200), (AGENT, 200), (CLIENT, 403)],
    ids=["admin", "agent", "client"],
)
def test_multi_role_route_rbac_matrix(client, settings, identity, expected):
    response = client.get("/_probe/staff", headers=bearer(settings, identity))

    assert response.status_code == expected


def test_role_gate_returns_401_before_403_for_an_anonymous_caller(client):
    """A caller with no session gets "log in", not "you are the wrong role"."""
    response = client.get("/_probe/admin-only")

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_role_gate_returns_401_for_a_disabled_admin(client, settings):
    with Session(client.app.state.engine) as session:
        admin = session.get(User, "u-admin-1")
        admin.status = "disabled"
        session.add(admin)
        session.commit()

    response = client.get("/_probe/admin-only", headers=bearer(settings, ADMIN))

    assert response.status_code == 401
    assert code_of(response) == "session_revoked"


# --- CSRF ---------------------------------------------------------------------


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_cookie_write_without_a_csrf_header_is_403_missing(client, settings, method):
    cookie_session(client, settings, ADMIN)

    response = getattr(client, method)("/_probe/write")

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_missing"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_cookie_write_with_a_wrong_csrf_header_is_403_invalid(client, settings, method):
    cookie_session(client, settings, ADMIN)

    response = getattr(client, method)(
        "/_probe/write", headers={"X-CSRF-Token": issue_csrf_token()}
    )

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_cookie_write_with_the_matching_csrf_header_succeeds(client, settings, method):
    csrf = cookie_session(client, settings, ADMIN)

    response = getattr(client, method)("/_probe/write", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200


def test_near_miss_csrf_header_is_rejected(client, settings):
    csrf = cookie_session(client, settings, ADMIN)

    response = client.post("/_probe/write", headers={"X-CSRF-Token": csrf[:-1]})

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


def test_csrf_header_from_a_different_session_is_rejected(client, settings):
    """Double-submit only works if the header is bound to *this* session's claim."""
    other_sessions_token = issue_csrf_token()
    cookie_session(client, settings, ADMIN)

    response = client.post("/_probe/write", headers={"X-CSRF-Token": other_sessions_token})

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


def test_cookie_session_without_a_csrf_claim_cannot_pass_csrf(client, settings):
    """An api-mode token replayed as a cookie has no claim to match against, so no
    header value can satisfy it."""
    token, _ = create_access_token(settings, user_id="u-admin-1", role="admin")
    client.cookies.set(settings.session_cookie_name, token)

    response = client.post("/_probe/write", headers={"X-CSRF-Token": issue_csrf_token()})

    assert response.status_code == 403
    assert code_of(response) == "csrf_token_invalid"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_bearer_writes_are_csrf_exempt(client, settings, method):
    """No ambient credential to forge, so CSRF does not apply regardless of method."""
    response = getattr(client, method)("/_probe/write", headers=bearer(settings, ADMIN))

    assert response.status_code == 200


def test_bearer_write_is_exempt_even_when_a_cookie_session_is_also_present(
    client, settings
):
    cookie_session(client, settings, CLIENT)

    response = client.post("/_probe/write", headers=bearer(settings, ADMIN))

    assert response.status_code == 200


@pytest.mark.parametrize("method", ["get", "head", "options"])
def test_safe_methods_are_csrf_exempt_for_cookie_sessions(client, settings, method):
    cookie_session(client, settings, ADMIN)

    response = getattr(client, method)("/_probe/write")

    assert response.status_code == 200


# --- ordering guarantee: 401 before 403 ---------------------------------------


@pytest.mark.parametrize(
    "csrf_header",
    [None, {"X-CSRF-Token": "wrong-value"}],
    ids=["missing-csrf", "wrong-csrf"],
)
def test_expired_cookie_session_is_401_not_403(client, tmp_path, settings, csrf_header):
    """Contract: session resolution runs first, so a stale session reads as "log in
    again" to the SPA rather than as a confusing CSRF failure. This test fails if the
    dependency order is ever flipped.
    """
    stale = make_db_settings(tmp_path, jwt_expire_minutes=-1)
    cookie_session(client, stale, ADMIN)

    response = client.post("/_probe/write", headers=csrf_header)

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_tampered_cookie_with_a_missing_csrf_header_is_401(client, settings):
    client.cookies.set(settings.session_cookie_name, "not-a-jwt")

    response = client.post("/_probe/write")

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"


def test_disabled_user_cookie_with_a_missing_csrf_header_is_401(
    client, settings, disabled_user
):
    cookie_session(client, settings, disabled_user)

    response = client.post("/_probe/write")

    assert response.status_code == 401
    assert code_of(response) == "session_revoked"


def test_anonymous_write_is_401_not_a_csrf_error(client):
    response = client.post("/_probe/write")

    assert response.status_code == 401
    assert code_of(response) == "not_authenticated"
