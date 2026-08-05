import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import make_settings

ALLOWED = "http://localhost:5173"
SECOND_ALLOWED = "https://admin.example.test"
DISALLOWED = "https://evil.example.test"


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def test_healthz_returns_ok(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_needs_no_authentication(client):
    """Phase 0 has no auth; the probe must stay open once auth lands."""
    assert client.get("/healthz").status_code == 200


def test_healthz_is_outside_the_api_v1_prefix(client):
    assert client.get("/api/v1/healthz").status_code == 404


def test_app_boots_without_a_dotenv_file(settings):
    """create_app(settings) must not fall back to get_settings()/.env."""
    app = create_app(settings)

    assert TestClient(app).get("/healthz").status_code == 200


# --- docs exposure ------------------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_exposed_in_dev(path):
    client = TestClient(create_app(make_settings(app_env="dev")))

    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_in_prod(path):
    client = TestClient(create_app(make_settings(app_env="prod")))

    assert client.get(path).status_code == 404


def test_healthz_still_works_in_prod():
    client = TestClient(create_app(make_settings(app_env="prod")))

    assert client.get("/healthz").json() == {"status": "ok"}


def test_openapi_schema_documents_healthz():
    client = TestClient(create_app(make_settings(app_env="dev")))

    schema = client.get("/openapi.json").json()

    assert "/healthz" in schema["paths"]


# --- CORS ---------------------------------------------------------------------


@pytest.fixture
def cors_client():
    settings = make_settings(cors_allowed_origins=f"{ALLOWED},{SECOND_ALLOWED}")
    return TestClient(create_app(settings))


@pytest.mark.parametrize("origin", [ALLOWED, SECOND_ALLOWED])
def test_configured_origin_is_allowed(cors_client, origin):
    response = cors_client.get("/healthz", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_credentials_are_allowed(cors_client):
    """Required for the cookie-authenticated admin SPA (README auth contracts)."""
    response = cors_client.get("/healthz", headers={"Origin": ALLOWED})

    assert response.headers["access-control-allow-credentials"] == "true"


def test_credentialed_response_never_uses_a_wildcard_origin(cors_client):
    response = cors_client.get("/healthz", headers={"Origin": ALLOWED})

    assert response.headers["access-control-allow-origin"] != "*"


def test_unconfigured_origin_gets_no_cors_headers(cors_client):
    response = cors_client.get("/healthz", headers={"Origin": DISALLOWED})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_from_configured_origin_is_approved(cors_client):
    response = cors_client.options(
        "/healthz",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()


def test_preflight_from_unconfigured_origin_is_rejected(cors_client):
    response = cors_client.options(
        "/healthz",
        headers={
            "Origin": DISALLOWED,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_500_responses_are_still_readable_by_the_admin_spa(cors_client):
    """Regression: a bare-Exception handler lands in ServerErrorMiddleware, outside
    CORSMiddleware, so its 500 carries no CORS headers and the SPA sees an opaque
    network failure. The catch-all must stay inside the CORS layer."""
    app = cors_client.app

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise ValueError("boom")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/_test/boom", headers={"Origin": ALLOWED}
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_request_without_origin_header_is_unaffected(cors_client):
    response = cors_client.get("/healthz")

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
