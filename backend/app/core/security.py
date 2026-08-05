"""Password hashing, session-token issue/decode, and CSRF token handling.

Deliberately HTTP-agnostic apart from raising the domain errors in
``app.core.exceptions`` — the FastAPI layer (``app/api/deps.py``) maps those to
responses. Extracting this module into a standalone Auth Service later
(README Design Decisions §1) therefore moves the file, not its call sites.
"""

import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import Settings
from app.core.exceptions import NotAuthenticatedError

BCRYPT_ROUNDS = 12
BCRYPT_MAX_PASSWORD_BYTES = 72
CSRF_TOKEN_BYTES = 32

# Verified against when no user matches the submitted email, so a failed login costs
# the same ~100ms of bcrypt work whether or not the account exists. Without it, the
# response-time difference is a user-enumeration oracle that the identical
# `invalid_credentials` message would otherwise close.
_ABSENT_USER_HASH = "$2b$12$N78Z.IR7OkxSF0wNfR8L0uEEbOBfd4q3ciZPUcnbU5xStgHXLVr9W"


class PasswordTooLongError(ValueError):
    """bcrypt truncates silently past 72 bytes, which would make two different
    passwords interchangeable. Reject rather than truncate."""


def hash_password(plain_password: str) -> str:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes when UTF-8 encoded."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    encoded = plain_password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, hashed_password.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash in the DB — treat as a failed login, not a 500.
        return False


def burn_password_verification() -> None:
    """Spend the same bcrypt cost as a real verification, for the unknown-email path."""
    verify_password(secrets.token_urlsafe(16), _ABSENT_USER_HASH)


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def csrf_tokens_match(expected: str | None, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    role: str
    expires_at: datetime
    # Present only on cookie sessions. Stored raw (not hashed) so GET /auth/me can
    # hand it back after a page reload wipes the SPA's in-memory copy — hashing would
    # add nothing, since anyone who can read the cookie already holds the session.
    csrf_token: str | None
    # Unique per issued token (secrets.token_urlsafe), for POST /auth/logout's
    # revocation denylist (app/core/revocation.py). Unlike `csrf`, every token gets
    # one unconditionally — both client_type=api and client_type=browser sessions
    # are revocable. Optional here (not `require`d by decode_access_token) rather
    # than mandatory: a token issued before this feature existed has no jti and
    # simply can never be revoked, which is accepted as-is (no real users predate
    # this feature) rather than rejecting such a token outright.
    jti: str | None


def create_access_token(
    settings: Settings,
    *,
    user_id: str,
    role: str,
    csrf_token: str | None = None,
) -> tuple[str, datetime]:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, object] = {
        "sub": user_id,
        "role": role,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if csrf_token is not None:
        payload["csrf"] = csrf_token
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(settings: Settings, token: str) -> TokenClaims:
    """Raises NotAuthenticatedError for anything unusable — expired, tampered,
    wrong algorithm, or missing required claims."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise NotAuthenticatedError() from exc
    except (OverflowError, TypeError, ValueError) as exc:
        # PyJWT coerces the temporal claims with a bare `int(payload[...])` while
        # validating exp/nbf/iat, so a hostile numeric claim (e.g. `exp: Infinity`)
        # escapes as OverflowError rather than a PyJWTError. Still a failed
        # authentication, not a server fault.
        raise NotAuthenticatedError() from exc

    role = payload.get("role")
    if not isinstance(payload["sub"], str) or not isinstance(role, str):
        raise NotAuthenticatedError()

    csrf_token = payload.get("csrf")
    jti = payload.get("jti")
    return TokenClaims(
        user_id=payload["sub"],
        role=role,
        expires_at=_expiry_from_claim(payload["exp"]),
        csrf_token=csrf_token if isinstance(csrf_token, str) else None,
        jti=jti if isinstance(jti, str) else None,
    )


def _expiry_from_claim(exp: object) -> datetime:
    """PyJWT's expiry check only needs `exp` to be int-coercible, so a string or an
    out-of-range value survives `jwt.decode` and would then raise TypeError/
    OverflowError out of `fromtimestamp`. An unusable claim is a failed
    authentication, not a server fault."""
    # float is allowed: RFC 7519's NumericDate permits non-integer values.
    # The bool arm is unreachable today rather than load-bearing — bool is an int
    # subclass coercing to 0 or 1, both of which PyJWT rejects as expired before
    # this runs. Kept so the type check stays honest if that ever changes.
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise NotAuthenticatedError()
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise NotAuthenticatedError() from error
