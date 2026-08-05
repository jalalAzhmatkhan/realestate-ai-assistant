"""Password hashing, JWT issue/decode, CSRF tokens, and the timing equalizer."""

import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import security
from app.core.exceptions import NotAuthenticatedError
from app.core.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    create_access_token,
    csrf_tokens_match,
    decode_access_token,
    hash_password,
    issue_csrf_token,
    verify_password,
)

from .conftest import make_settings

PASSWORD = "ChangeMe123!"


# --- password hashing ---------------------------------------------------------


def test_hash_and_verify_round_trip():
    assert verify_password(PASSWORD, hash_password(PASSWORD)) is True


def test_wrong_password_does_not_verify():
    assert verify_password("WrongPassword1!", hash_password(PASSWORD)) is False


def test_hashes_are_salted_per_call():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_hash_is_bcrypt_at_the_configured_cost():
    assert hash_password(PASSWORD).startswith(f"$2b${security.BCRYPT_ROUNDS:02d}$")


def test_password_at_the_byte_limit_is_accepted():
    at_limit = "a" * BCRYPT_MAX_PASSWORD_BYTES

    assert verify_password(at_limit, hash_password(at_limit)) is True


def test_password_over_the_byte_limit_is_rejected_on_hashing():
    """bcrypt truncates silently past 72 bytes, which would make two different
    passwords interchangeable."""
    with pytest.raises(PasswordTooLongError):
        hash_password("a" * (BCRYPT_MAX_PASSWORD_BYTES + 1))


def test_byte_limit_counts_utf8_bytes_not_characters():
    """37 two-byte characters are only 37 characters but 74 bytes."""
    over_limit = "é" * 37
    assert len(over_limit) < BCRYPT_MAX_PASSWORD_BYTES
    assert len(over_limit.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES

    with pytest.raises(PasswordTooLongError):
        hash_password(over_limit)
    assert verify_password("é" * 36, hash_password("é" * 36)) is True


def test_over_length_verification_attempt_returns_false_not_an_exception():
    """An attacker submitting a 10KB password must get a clean 401, not a 500."""
    stored = hash_password(PASSWORD)

    assert verify_password("a" * 5000, stored) is False


def test_over_length_attempt_is_rejected_even_when_its_prefix_is_correct():
    """Guards the truncation equivalence bcrypt would otherwise allow."""
    stored = hash_password("a" * BCRYPT_MAX_PASSWORD_BYTES)

    assert verify_password("a" * (BCRYPT_MAX_PASSWORD_BYTES + 1), stored) is False


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$2b$12$short", "$$$$"])
def test_malformed_stored_hash_returns_false_not_an_exception(garbage):
    assert verify_password(PASSWORD, garbage) is False


# --- unknown-email timing equalizer -------------------------------------------


def test_burn_password_verification_calls_into_bcrypt(monkeypatch):
    calls = []
    monkeypatch.setattr(
        security,
        "verify_password",
        lambda plain, hashed: calls.append((plain, hashed)) or False,
    )

    security.burn_password_verification()

    assert len(calls) == 1
    assert calls[0][1] == security._ABSENT_USER_HASH


def test_burn_uses_a_hash_at_the_same_cost_as_a_real_one():
    """A cheaper decoy hash would leave the timing oracle open."""
    assert security._ABSENT_USER_HASH.startswith(f"$2b${security.BCRYPT_ROUNDS:02d}$")


def test_burn_password_verification_actually_spends_bcrypt_time():
    started = time.perf_counter()
    security.burn_password_verification()
    burn_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    verify_password("WrongPassword1!", security._ABSENT_USER_HASH)
    real_elapsed = time.perf_counter() - started

    assert burn_elapsed > 0.01, "burn is a no-op — the enumeration oracle is open"
    assert burn_elapsed > real_elapsed / 4


# --- CSRF tokens --------------------------------------------------------------


def test_issued_csrf_tokens_are_unpredictable_and_long_enough():
    tokens = {issue_csrf_token() for _ in range(20)}

    assert len(tokens) == 20
    assert all(len(token) >= 43 for token in tokens)


def test_matching_csrf_tokens_compare_true():
    token = issue_csrf_token()

    assert csrf_tokens_match(token, token) is True


@pytest.mark.parametrize(
    ("expected", "provided"),
    [
        ("abc", "abd"),
        ("abc", "ab"),
        ("abc", "abcd"),
        ("abc", ""),
        ("", "abc"),
        (None, "abc"),
        ("abc", None),
        (None, None),
        ("", ""),
    ],
)
def test_non_matching_csrf_tokens_compare_false(expected, provided):
    assert csrf_tokens_match(expected, provided) is False


def test_csrf_comparison_is_constant_time():
    """hmac.compare_digest, not ==, so a token cannot be recovered byte by byte."""
    token = issue_csrf_token()
    near_miss = token[:-1] + ("A" if token[-1] != "A" else "B")

    assert csrf_tokens_match(token, near_miss) is False


# --- JWT ----------------------------------------------------------------------


def test_access_token_round_trips_its_claims():
    settings = make_settings()

    token, expires_at = create_access_token(settings, user_id="u-admin-1", role="admin")
    claims = decode_access_token(settings, token)

    assert claims.user_id == "u-admin-1"
    assert claims.role == "admin"
    assert claims.expires_at == expires_at.replace(microsecond=0)
    assert claims.expires_at.tzinfo is not None


def test_expiry_follows_the_configured_lifetime():
    settings = make_settings(jwt_expire_minutes=15)

    _, expires_at = create_access_token(settings, user_id="u-1", role="client")

    delta = expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=14) < delta <= timedelta(minutes=15)


def test_csrf_claim_is_absent_unless_explicitly_requested():
    settings = make_settings()

    token, _ = create_access_token(settings, user_id="u-1", role="client")

    assert "csrf" not in jwt.decode(token, options={"verify_signature": False})
    assert decode_access_token(settings, token).csrf_token is None


def test_csrf_claim_round_trips_when_supplied():
    settings = make_settings()
    csrf = issue_csrf_token()

    token, _ = create_access_token(settings, user_id="u-1", role="client", csrf_token=csrf)

    assert decode_access_token(settings, token).csrf_token == csrf


def test_token_signed_with_another_secret_is_rejected():
    token, _ = create_access_token(
        make_settings(jwt_secret_key="attacker-secret"), user_id="u-1", role="admin"
    )

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(make_settings(jwt_secret_key="real-secret"), token)


def test_tampered_token_is_rejected():
    settings = make_settings()
    token, _ = create_access_token(settings, user_id="u-client-1", role="client")
    header, payload, signature = token.split(".")

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, f"{header}.{payload}x.{signature}")


def test_expired_token_is_rejected():
    settings = make_settings(jwt_expire_minutes=-1)
    token, _ = create_access_token(settings, user_id="u-1", role="admin")

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(make_settings(), token)


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "admin", "exp": 9999999999},
        {"sub": "u-1", "role": "admin"},
        {"sub": "u-1", "exp": 9999999999},
        {"sub": 12345, "role": "admin", "exp": 9999999999},
        {"sub": "u-1", "role": 42, "exp": 9999999999},
    ],
    ids=["no-sub", "no-exp", "no-role", "non-string-sub", "non-string-role"],
)
def test_tokens_missing_required_claims_are_rejected(payload):
    settings = make_settings()
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, token)


def test_token_signed_with_a_different_algorithm_is_rejected():
    """Algorithm confusion: decode must pin the configured alg, not trust the header."""
    settings = make_settings(jwt_algorithm="HS256")
    token = jwt.encode(
        {"sub": "u-1", "role": "admin", "exp": 9999999999},
        settings.jwt_secret_key,
        algorithm="HS384",
    )

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, token)


def test_unsigned_alg_none_token_is_rejected():
    settings = make_settings()
    token = jwt.encode({"sub": "u-admin-1", "role": "admin", "exp": 9999999999}, key="", algorithm="none")

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, token)


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer x.y.z"])
def test_unparseable_tokens_are_rejected(token):
    with pytest.raises(NotAuthenticatedError):
        decode_access_token(make_settings(), token)


def _signed(settings, **claims) -> str:
    payload = {"sub": "u-admin-1", "role": "admin", **claims}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest.mark.parametrize(
    "exp",
    [10**19, "9999999999", True, False, None, [9999999999], {"exp": 1}],
    ids=[
        "out-of-range",
        "non-integer",
        # bool is an int subclass: `exp: true` must not quietly become
        # 1970-01-01T00:00:01Z, i.e. a permanently-expired token that never errors.
        "bool-true",
        "bool-false",
        "null",
        "list",
        "object",
    ],
)
def test_unusable_exp_claim_is_rejected_not_raised(exp):
    """Claims that survive `jwt.decode` and fail in `_expiry_from_claim`.

    The docstring promises NotAuthenticatedError "for anything unusable"; before the
    DEFECT-1 fix these escaped as OverflowError/TypeError and rendered as 500.
    """
    settings = make_settings()

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, _signed(settings, exp=exp))


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": float("inf")},
        {"exp": float("-inf")},
        {"exp": float("nan")},
        {"exp": 9999999999, "nbf": float("inf")},
        {"exp": 9999999999, "nbf": "later"},
        {"exp": 9999999999, "nbf": 10**19},
        {"exp": 9999999999, "iat": float("inf")},
        {"exp": 9999999999, "iat": "yesterday"},
    ],
    ids=[
        "exp-inf",
        "exp-negative-inf",
        "exp-nan",
        "nbf-inf",
        "nbf-string",
        "nbf-out-of-range",
        "iat-inf",
        "iat-string",
    ],
)
def test_hostile_temporal_claims_inside_jwt_decode_are_rejected_not_raised(claims):
    """A distinct code path from the test above: PyJWT validates exp/nbf/iat with a
    bare `int(...)`, and the resulting OverflowError/TypeError/ValueError is *not* a
    `jwt.PyJWTError`, so these never reach `_expiry_from_claim` at all. Guarding only
    the fromtimestamp conversion would leave every one of these returning 500.
    """
    settings = make_settings()

    with pytest.raises(NotAuthenticatedError):
        decode_access_token(settings, _signed(settings, **claims))


@pytest.mark.parametrize("exp", [9999999999, 9999999999.5], ids=["int", "float"])
def test_legitimate_numericdate_types_still_decode(exp):
    """Guard-not-over-broad regression: RFC 7519's NumericDate permits non-integer
    values, so rejecting floats would turn a valid third-party token into a 401."""
    settings = make_settings()

    claims = decode_access_token(settings, _signed(settings, exp=exp))

    assert claims.expires_at.timestamp() == exp


def test_a_misconfigured_algorithm_is_still_handled_by_the_pyjwt_branch():
    """Bounds the widened `except (OverflowError, TypeError, ValueError)`: every
    realistic misconfiguration (bad algorithm, empty secret) raises a PyJWTError and
    was already a 401 before that clause existed, so the clause did not broaden what
    a config error looks like — it only added hostile-input coverage.
    """
    settings = make_settings()
    token = _signed(settings, exp=9999999999)

    for broken in (make_settings(jwt_algorithm="HS999"), make_settings(jwt_secret_key="")):
        with pytest.raises(jwt.PyJWTError):
            jwt.decode(
                token,
                broken.jwt_secret_key,
                algorithms=[broken.jwt_algorithm],
                options={"require": ["sub", "exp"]},
            )
        with pytest.raises(NotAuthenticatedError):
            decode_access_token(broken, token)


def test_non_string_csrf_claim_is_read_as_absent():
    settings = make_settings()
    token = jwt.encode(
        {"sub": "u-1", "role": "admin", "exp": 9999999999, "csrf": 1234},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    assert decode_access_token(settings, token).csrf_token is None
