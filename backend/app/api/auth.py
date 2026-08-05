import logging

from fastapi import APIRouter, Request, Response, status
from sqlmodel import select

from app.api.deps import (
    DbSession,
    SessionContextDep,
    SettingsDep,
    check_csrf,
    resolve_session_or_none,
)
from app.core.config import Settings
from app.core.exceptions import AccountDisabledError, InvalidCredentialsError
from app.core.security import (
    burn_password_verification,
    create_access_token,
    issue_csrf_token,
    verify_password,
)
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, SessionInfo, UserPublic

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
) -> LoginResponse:
    """Exchange credentials for a session.

    `client_type=api` returns a Bearer JWT; `client_type=browser` sets an httpOnly
    session cookie and returns a CSRF token instead. Unknown email and wrong password
    are indistinguishable in both status and message, so this is not a user-
    enumeration oracle.
    """
    email = payload.email.strip().lower()
    user = db.exec(select(User).where(User.email == email)).first()

    if user is None:
        # Equalize response time with the wrong-password path.
        burn_password_verification()
        raise InvalidCredentialsError()

    if not verify_password(payload.password, user.hashed_password):
        raise InvalidCredentialsError()

    # Checked only after the password verifies: rejecting earlier would let anyone
    # discover that an address exists and is disabled without holding its password.
    if user.status != "active":
        raise AccountDisabledError()

    csrf_token = issue_csrf_token() if payload.client_type == "browser" else None
    token, expires_at = create_access_token(
        settings, user_id=user.id, role=user.role, csrf_token=csrf_token
    )
    expires_in = settings.jwt_expire_minutes * 60

    logger.info(
        "login_succeeded",
        extra={"user_id": user.id, "role": user.role, "client_type": payload.client_type},
    )

    if payload.client_type == "browser":
        _set_session_cookie(response, settings, token, expires_in)
        return LoginResponse(
            access_token=None,
            token_type=None,
            expires_in=expires_in,
            csrf_token=csrf_token,
            user=UserPublic.model_validate(user, from_attributes=True),
        )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        csrf_token=None,
        user=UserPublic.model_validate(user, from_attributes=True),
    )


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def logout(request: Request, db: DbSession, settings: SettingsDep) -> Response:
    """Idempotent: an absent, expired, or already-revoked session still returns 204,
    so a double-click or a stale tab does not surface an error.

    Bearer sessions get a 204 with no cookie header — MVP JWTs are stateless and are
    not added to a denylist (ruling A3; the 60-minute TTL is the mitigation).
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)

    context = resolve_session_or_none(request, db, settings)
    if context is not None:
        # A live cookie session is worth protecting here too: without this, a
        # cross-site form could log the user out.
        check_csrf(request, context)
        logger.info(
            "logout", extra={"user_id": context.user.id, "auth_method": context.auth_method}
        )

    if settings.session_cookie_name in request.cookies:
        _clear_session_cookie(response, settings)

    return response


@router.get("/me", response_model=MeResponse)
def read_current_session(context: SessionContextDep) -> MeResponse:
    """Identity plus a fresh copy of the CSRF token.

    The SPA cannot decode the httpOnly cookie, and a full page reload wipes its
    in-memory CSRF token while the cookie survives — without returning the token
    here, the user would look logged in but every write would fail
    `csrf_token_missing`.
    """
    user = context.user
    return MeResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        session=SessionInfo(
            auth_method=context.auth_method,
            expires_at=context.expires_at,
            csrf_token=context.csrf_token,
        ),
    )


def _set_session_cookie(
    response: Response, settings: Settings, token: str, max_age: int
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
    )
