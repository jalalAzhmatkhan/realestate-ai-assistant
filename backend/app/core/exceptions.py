import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

INTERNAL_ERROR_CODE = "internal_error"
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


class DomainError(Exception):
    """Base for every error the API renders as {"detail": {"code", "message", ...}}.

    Subclasses set `code` and `status_code`; callers pass a human-facing `message`
    plus any error-specific extra fields (e.g. `suggested_alternatives=[...]`),
    which are merged into the `detail` object. Clients branch on `code`, never on
    `message`.
    """

    code: str = INTERNAL_ERROR_CODE
    status_code: int = 500

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class NotAuthenticatedError(DomainError):
    code = "not_authenticated"
    status_code = 401

    def __init__(self, message: str = "Authentication required.", **extra: Any) -> None:
        super().__init__(message, **extra)


class InvalidCredentialsError(DomainError):
    code = "invalid_credentials"
    status_code = 401

    def __init__(
        self, message: str = "Incorrect email or password.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class SessionRevokedError(DomainError):
    """The token is still valid but the account behind it no longer is (deleted or
    disabled mid-session). 401 rather than 403 so the SPA re-logs-in instead of
    leaving a disabled account with a working UI until the JWT expires."""

    code = "session_revoked"
    status_code = 401

    def __init__(
        self, message: str = "This session is no longer valid. Please log in again.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class AccountDisabledError(DomainError):
    code = "account_disabled"
    status_code = 403

    def __init__(
        self, message: str = "This account has been disabled.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class ForbiddenError(DomainError):
    """Role-level denial: the caller's role may not touch this endpoint at all.
    Out-of-scope *individual records* return 404 instead, so their ids cannot be
    enumerated by probing for a permission error."""

    code = "forbidden"
    status_code = 403

    def __init__(
        self, message: str = "You do not have access to this resource.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class CsrfTokenMissingError(DomainError):
    code = "csrf_token_missing"
    status_code = 403

    def __init__(
        self, message: str = "X-CSRF-Token header is required for this request.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class CsrfTokenInvalidError(DomainError):
    code = "csrf_token_invalid"
    status_code = 403

    def __init__(
        self, message: str = "X-CSRF-Token header does not match this session.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class ResourceNotFoundError(DomainError):
    """One answer for "does not exist" and "not yours".

    Individual records outside the caller's scope return this rather than
    :class:`ForbiddenError`, so ids cannot be enumerated by probing for a permission
    error. `403` stays reserved for role-level denials.
    """

    status_code = 404


class PropertyNotFoundError(ResourceNotFoundError):
    code = "property_not_found"

    def __init__(
        self, message: str = "That property could not be found.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class BookingNotFoundError(ResourceNotFoundError):
    code = "booking_not_found"

    def __init__(
        self, message: str = "That booking could not be found.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class UserNotFoundError(ResourceNotFoundError):
    code = "user_not_found"

    def __init__(
        self, message: str = "That user could not be found.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class EmailAlreadyExistsError(DomainError):
    code = "email_already_exists"
    status_code = 409

    def __init__(
        self, message: str = "That email address is already registered.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class InvalidAgentIdError(DomainError):
    """A body field naming a user who cannot own a listing.

    422 rather than 404: the missing thing is a *field* inside the request, not the
    resource the request addresses, so a 404 would be read as "the property is gone".
    It also lets the SPA attach the message to its `agent_id` form field.
    """

    code = "invalid_agent_id"
    status_code = 422

    def __init__(
        self,
        message: str = "agent_id must reference an existing agent or admin user.",
        **extra: Any,
    ) -> None:
        super().__init__(message, **extra)


class SelfLockoutForbiddenError(DomainError):
    """An admin's ``PATCH`` on their own account would disable it or take away the
    ``admin`` role.

    409, not 422: the request body is well-formed, and the field values are individually
    valid — the conflict is between the request and the fact that its target is the
    caller's own, currently-authenticated account. There is no self-service recovery
    (users are disabled, never deleted, and there is no "re-enable my own account" path),
    so this is rejected outright rather than allowed-and-regretted.
    """

    code = "self_lockout_forbidden"
    status_code = 409

    def __init__(
        self,
        message: str = (
            "You cannot disable your own account or remove your own admin role."
        ),
        **extra: Any,
    ) -> None:
        super().__init__(message, **extra)


class InvalidPriceRangeError(DomainError):
    code = "invalid_price_range"
    status_code = 422

    def __init__(
        self, message: str = "min_price must not exceed max_price.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class InvalidDateRangeError(DomainError):
    code = "invalid_date_range"
    status_code = 422

    def __init__(
        self, message: str = "date_from must not be later than date_to.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class PageSizeTooLargeError(DomainError):
    """`page_size` above MAX_PAGE_SIZE is rejected rather than silently clamped —
    a clamped page looks to the client like the full page it asked for, so it stops
    paginating and silently loses rows."""

    code = "page_size_too_large"
    status_code = 422

    def __init__(self, message: str = "page_size exceeds the maximum allowed.", **extra: Any) -> None:
        super().__init__(message, **extra)


class InvalidSortFieldError(DomainError):
    code = "invalid_sort_field"
    status_code = 422

    def __init__(
        self, message: str = "That sort field is not supported by this endpoint.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class EvalSetUnavailableError(DomainError):
    code = "eval_set_unavailable"
    status_code = 503

    def __init__(
        self, message: str = "The retrieval evaluation set is unavailable.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class FaqIndexUnavailableError(DomainError):
    code = "faq_index_unavailable"
    status_code = 503

    def __init__(
        self,
        message: str = "The FAQ retrieval index has no rows for the configured embedding model.",
        **extra: Any,
    ) -> None:
        super().__init__(message, **extra)


class InvalidKValueError(DomainError):
    code = "invalid_k_value"
    status_code = 422

    def __init__(
        self, message: str = "k_values must be non-empty and each between 1 and 10.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class InvalidTierError(DomainError):
    code = "invalid_tier"
    status_code = 422

    def __init__(self, message: str = "Unknown evaluation tier.", **extra: Any) -> None:
        super().__init__(message, **extra)


class EvalRunFailedError(DomainError):
    """The run failed mid-flight. ``detail.run_id`` names the row already persisted
    with ``status="failed"``, so the caller can look up what happened."""

    code = "eval_run_failed"
    status_code = 502

    def __init__(self, message: str = "The evaluation run failed.", **extra: Any) -> None:
        super().__init__(message, **extra)


class EvalRunNotFoundError(ResourceNotFoundError):
    code = "eval_run_not_found"

    def __init__(
        self, message: str = "That evaluation run could not be found.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class InvalidScoreRangeError(DomainError):
    code = "invalid_score_range"
    status_code = 422

    def __init__(
        self, message: str = "min_score must not exceed max_score.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


class FaithfulnessCheckNotFoundError(ResourceNotFoundError):
    code = "faithfulness_check_not_found"

    def __init__(
        self, message: str = "That faithfulness check could not be found.", **extra: Any
    ) -> None:
        super().__init__(message, **extra)


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    logger.warning(
        "domain_error",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method,
        },
    )
    # jsonable_encoder, not the raw dict: `extra` is arbitrary error-specific payload,
    # and `suggested_alternatives` carries datetimes that json.dumps rejects — without
    # this a slot conflict renders as a 500 instead of its 409.
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder({"detail": exc.to_detail()}),
    )


async def catch_unhandled_exceptions(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Catch-all for anything no exception handler claimed.

    This is middleware rather than `add_exception_handler(Exception, ...)` because
    Starlette routes a bare-`Exception` handler into ServerErrorMiddleware, which sits
    outside every `add_middleware()` layer — its response bypasses CORSMiddleware and
    the browser sees an opaque CORS failure instead of the `internal_error` envelope.
    """
    try:
        return await call_next(request)
    except Exception:
        logger.exception(
            "unhandled_exception",
            extra={
                "error_code": INTERNAL_ERROR_CODE,
                "path": request.url.path,
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": INTERNAL_ERROR_CODE,
                    "message": INTERNAL_ERROR_MESSAGE,
                }
            },
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire domain errors and the catch-all. Pydantic's 422 handler is left alone
    so validation errors keep their native field-level list for the SPA.

    Must be called *before* CORSMiddleware is added: `add_middleware()` inserts at the
    top of the stack, so the last-added middleware is the outermost one, and CORS has
    to be outside the catch-all to stamp headers onto its 500 responses.
    """
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_middleware(BaseHTTPMiddleware, dispatch=catch_unhandled_exceptions)
