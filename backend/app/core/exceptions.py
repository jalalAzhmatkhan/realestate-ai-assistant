import logging
from typing import Any

from fastapi import FastAPI, Request, Response
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
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})


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
