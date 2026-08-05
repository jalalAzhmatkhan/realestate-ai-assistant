import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.exceptions import (
    DomainError,
    InvalidCredentialsError,
    NotAuthenticatedError,
)
from app.main import create_app

from .conftest import make_settings

LEAKY_MESSAGE = "connection string postgres://user:hunter2@db.internal/prod"


class _Body(BaseModel):
    page_size: int


class SlotConflictError(DomainError):
    code = "booking_slot_conflict"
    status_code = 409


def build_error_app(**settings_overrides) -> FastAPI:
    """The real app plus throwaway routes that raise. Application code is untouched."""
    app = create_app(make_settings(**settings_overrides))

    @app.get("/_test/not-authenticated")
    async def _not_authenticated() -> None:
        raise NotAuthenticatedError()

    @app.get("/_test/invalid-credentials")
    async def _invalid_credentials() -> None:
        raise InvalidCredentialsError()

    @app.get("/_test/custom-message")
    async def _custom_message() -> None:
        raise NotAuthenticatedError("Session expired, please log in again.")

    @app.get("/_test/with-extra")
    async def _with_extra() -> None:
        raise SlotConflictError(
            "That slot is already booked.",
            suggested_alternatives=[{"availability_slot_id": "avail-002"}],
        )

    @app.get("/_test/boom")
    async def _boom() -> None:
        raise ValueError(LEAKY_MESSAGE)

    @app.post("/_test/validated")
    async def _validated(body: _Body) -> dict[str, int]:
        return {"page_size": body.page_size}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_error_app(), raise_server_exceptions=False)


# --- DomainError envelope -----------------------------------------------------


@pytest.mark.parametrize(
    "path,status,code",
    [
        ("/_test/not-authenticated", 401, "not_authenticated"),
        ("/_test/invalid-credentials", 401, "invalid_credentials"),
    ],
)
def test_domain_error_renders_the_documented_envelope(client, path, status, code):
    response = client.get(path)

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"detail"}
    assert body["detail"]["code"] == code
    assert isinstance(body["detail"]["message"], str)
    assert body["detail"]["message"]


def test_domain_error_honours_a_caller_supplied_message(client):
    body = client.get("/_test/custom-message").json()

    assert body["detail"] == {
        "code": "not_authenticated",
        "message": "Session expired, please log in again.",
    }


def test_domain_error_merges_error_specific_extra_fields(client):
    response = client.get("/_test/with-extra")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "booking_slot_conflict",
        "message": "That slot is already booked.",
        "suggested_alternatives": [{"availability_slot_id": "avail-002"}],
    }


def test_to_detail_shape_without_http():
    error = SlotConflictError("nope", suggested_alternatives=[])

    assert error.to_detail() == {
        "code": "booking_slot_conflict",
        "message": "nope",
        "suggested_alternatives": [],
    }


def test_domain_error_subclasses_carry_their_own_code_and_status():
    assert (NotAuthenticatedError.code, NotAuthenticatedError.status_code) == (
        "not_authenticated",
        401,
    )
    assert (InvalidCredentialsError.code, InvalidCredentialsError.status_code) == (
        "invalid_credentials",
        401,
    )


def test_invalid_credentials_message_is_not_a_user_enumeration_oracle():
    """README: unknown email and wrong password must be indistinguishable."""
    message = InvalidCredentialsError().message.lower()

    assert "email" in message and "password" in message
    assert "not found" not in message
    assert "unknown" not in message


# --- unhandled exceptions -----------------------------------------------------


def test_unhandled_exception_returns_the_generic_envelope(client):
    response = client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
        }
    }


def test_unhandled_exception_leaks_neither_message_nor_traceback(client):
    body = client.get("/_test/boom").text

    assert "hunter2" not in body
    assert "postgres://" not in body
    assert "ValueError" not in body
    assert "Traceback" not in body
    assert "_test/boom" not in body
    assert ".py" not in body


def test_unhandled_exception_is_logged_with_the_stack_trace(client, caplog):
    with caplog.at_level("ERROR"):
        client.get("/_test/boom")

    records = [r for r in caplog.records if r.message == "unhandled_exception"]
    assert records, "the catch-all handler must log the failure it swallows"
    assert records[0].exc_info is not None


# --- validation errors are left native ----------------------------------------


def test_validation_errors_keep_fastapis_native_field_list(client):
    response = client.post("/_test/validated", json={"page_size": "not-an-int"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list), "422 must stay a field-level list for the SPA"
    assert detail[0]["loc"] == ["body", "page_size"]


def test_handlers_are_registered_in_prod_too():
    prod_client = TestClient(
        build_error_app(app_env="prod"), raise_server_exceptions=False
    )

    assert prod_client.get("/_test/not-authenticated").json()["detail"]["code"] == (
        "not_authenticated"
    )
    assert prod_client.get("/_test/boom").json()["detail"]["code"] == "internal_error"
