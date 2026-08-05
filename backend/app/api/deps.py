"""Authentication, RBAC, and CSRF dependencies.

Ordering is contractual, not incidental: session resolution runs first and raises
``401`` for a missing/expired/revoked session, so CSRF (``403``) can only ever be
reached by a caller whose session is valid. A stale session therefore reads as
"log in again" to the SPA rather than as a confusing CSRF failure.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Request
from sqlmodel import Session

from app.core.config import Settings
from app.core.exceptions import (
    CsrfTokenInvalidError,
    CsrfTokenMissingError,
    ForbiddenError,
    NotAuthenticatedError,
    SessionRevokedError,
)
from app.core.revocation import TokenDenylist
from app.core.security import csrf_tokens_match, decode_access_token
from app.db.session import get_session
from app.models import User, UserRole

AuthMethod = Literal["cookie", "bearer"]
CSRF_HEADER = "X-CSRF-Token"
BEARER_PREFIX = "bearer "
# Safe methods change no state, so there is nothing for a forged cross-site
# request to accomplish.
CSRF_EXEMPT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class SessionContext:
    user: User
    auth_method: AuthMethod
    expires_at: datetime
    # Non-null only for cookie sessions: a Bearer caller has no ambient credential
    # to forge, so CSRF does not apply to it at all.
    csrf_token: str | None
    # The token's jti (app/core/revocation.py), so POST /auth/logout can revoke the
    # exact token presented rather than every session belonging to this user. None
    # only for a token issued before the revocation feature existed (see
    # TokenClaims.jti) — such a token authenticates as before but can never be
    # individually revoked.
    jti: str | None


def get_app_settings(request: Request) -> Settings:
    """Read the Settings the app was built with rather than the cached global, so
    ``create_app(settings)`` in a test is actually honored downstream."""
    return request.app.state.settings


def get_token_denylist(request: Request) -> TokenDenylist:
    """Read the denylist the app was built with, same rationale as
    ``get_app_settings`` — a test can substitute its own binding
    (``create_app(settings, token_denylist=...)``) without a global to reset."""
    return request.app.state.token_denylist


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[Session, Depends(get_session)]
TokenDenylistDep = Annotated[TokenDenylist, Depends(get_token_denylist)]


def _read_credential(request: Request, settings: Settings) -> tuple[str, AuthMethod] | None:
    """Bearer wins over the cookie when both are present — an explicit header is a
    deliberate credential choice, while the cookie may just be ambient."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith(BEARER_PREFIX):
        token = header[len(BEARER_PREFIX) :].strip()
        if token:
            return token, "bearer"

    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return cookie, "cookie"
    return None


def get_session_context(
    request: Request, db: DbSession, settings: SettingsDep, denylist: TokenDenylistDep
) -> SessionContext:
    credential = _read_credential(request, settings)
    if credential is None:
        raise NotAuthenticatedError()
    token, auth_method = credential

    # Raises NotAuthenticatedError for expired, tampered, or malformed tokens.
    claims = decode_access_token(settings, token)

    # A revoked token must read exactly like an expired/invalid one to the caller
    # (401 not_authenticated), never as a distinguishable "this token was explicitly
    # revoked" state. A claims.jti of None means this token predates the revocation
    # feature (see TokenClaims.jti) and cannot be checked — it authenticates as
    # before rather than being rejected outright.
    if claims.jti is not None and denylist.is_revoked(claims.jti):
        raise NotAuthenticatedError()

    user = db.get(User, claims.user_id)
    if user is None or user.status != "active":
        # Distinct from not_authenticated: the token is intact, the account is not.
        # Still a 401 so the SPA re-logs-in instead of leaving a disabled account
        # with a working UI until the JWT expires.
        raise SessionRevokedError()

    return SessionContext(
        user=user,
        auth_method=auth_method,
        expires_at=claims.expires_at,
        csrf_token=claims.csrf_token if auth_method == "cookie" else None,
        jti=claims.jti,
    )


def resolve_session_or_none(
    request: Request, db: DbSession, settings: SettingsDep, denylist: TokenDenylistDep
) -> SessionContext | None:
    """Session resolution that tolerates a missing or unusable credential.

    Only ``POST /auth/logout`` needs this: it must return 204 for an already-expired
    (or already-revoked) session rather than the 401 every other endpoint owes the
    caller.
    """
    try:
        return get_session_context(request, db, settings, denylist)
    except (NotAuthenticatedError, SessionRevokedError):
        return None


SessionContextDep = Annotated[SessionContext, Depends(get_session_context)]


def get_current_user(context: SessionContextDep) -> User:
    return context.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole) -> Callable[[SessionContext], User]:
    """Role-level gate. Denials are 403 — a role that may not touch the endpoint at
    all is not information worth hiding, unlike an out-of-scope individual record,
    which returns 404 so its id cannot be enumerated."""
    allowed = frozenset(roles)

    def dependency(context: SessionContextDep) -> User:
        if context.user.role not in allowed:
            raise ForbiddenError()
        return context.user

    return dependency


def check_csrf(request: Request, context: SessionContext) -> None:
    """Double-submit check for cookie-authenticated state-changing requests.

    The expected value is the ``csrf`` claim inside the signed session JWT, so
    validation needs no server-side state — no Redis, no sticky sessions.
    """
    if request.method.upper() in CSRF_EXEMPT_METHODS:
        return
    if context.auth_method != "cookie":
        return

    submitted = request.headers.get(CSRF_HEADER)
    if not submitted:
        raise CsrfTokenMissingError()
    if not csrf_tokens_match(context.csrf_token, submitted):
        raise CsrfTokenInvalidError()


def enforce_csrf(request: Request, context: SessionContextDep) -> None:
    """Router-level guard: ``APIRouter(dependencies=[Depends(enforce_csrf)])`` so an
    individual write endpoint cannot forget it."""
    check_csrf(request, context)
