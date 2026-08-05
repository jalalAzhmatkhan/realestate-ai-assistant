"""app/core/revocation.py -- the POST /auth/logout JWT denylist.

Verified against fakeredis (an in-memory Redis-protocol server) rather than a real
Redis instance, which is not available in this environment; QA decides separately
whether the full test suite keeps using fakeredis or a real Redis fixture (see the
note on backend/pyproject.toml's dev dependency group).
"""

import logging

import fakeredis
import pytest
import redis

from app.core.revocation import RedisTokenDenylist

# --- SETEX-backed revoke/is_revoked round trip ---------------------------------


@pytest.fixture
def denylist() -> RedisTokenDenylist:
    return RedisTokenDenylist(fakeredis.FakeRedis())


def test_an_unrevoked_jti_is_not_revoked(denylist):
    assert denylist.is_revoked("jti-1") is False


def test_a_revoked_jti_is_revoked(denylist):
    denylist.revoke("jti-1", ttl_seconds=60)

    assert denylist.is_revoked("jti-1") is True


def test_revoking_one_jti_does_not_affect_another(denylist):
    denylist.revoke("jti-1", ttl_seconds=60)

    assert denylist.is_revoked("jti-2") is False


def test_revoking_with_a_non_positive_ttl_is_a_no_op(denylist):
    """An already-expired token has nothing left to protect, and Redis's SETEX
    rejects a non-positive TTL outright -- guard before ever calling it."""
    denylist.revoke("jti-1", ttl_seconds=0)
    denylist.revoke("jti-2", ttl_seconds=-5)

    assert denylist.is_revoked("jti-1") is False
    assert denylist.is_revoked("jti-2") is False


def test_the_denylist_entry_expires_with_the_configured_ttl():
    """The whole point of SETEX over SET: the entry disappears on its own, at
    (approximately) the token's original `exp`, so the denylist can never grow
    unbounded -- there is no cleanup job."""
    client = fakeredis.FakeRedis()
    RedisTokenDenylist(client).revoke("jti-1", ttl_seconds=60)

    ttl = client.ttl(b"revoked_jti:jti-1")

    assert 0 < ttl <= 60


# --- adversarial jti values ----------------------------------------------------
#
# A jti reaching this module comes from a signature-verified token, so these are not
# attacker-reachable without the signing key. They are pinned anyway because the jti
# is concatenated straight into a Redis key: the property worth holding is that no
# jti value can make one token's revocation apply to a different token.


@pytest.mark.parametrize(
    "jti",
    [
        "",
        "with:colons",
        "revoked_jti:looks-like-a-prefixed-key",
        "spaces and \n newlines",
        "unicode-é中文",
        "x" * 4096,
    ],
    ids=["empty", "colons", "prefix-lookalike", "whitespace", "unicode", "very-long"],
)
def test_an_unusual_jti_round_trips_without_error(denylist, jti):
    denylist.revoke(jti, ttl_seconds=60)

    assert denylist.is_revoked(jti) is True


def test_a_prefix_lookalike_jti_does_not_revoke_the_key_it_imitates(denylist):
    """`revoked_jti:` is prepended, never parsed back out, so a jti that itself starts
    with the prefix cannot reach another token's denylist entry."""
    denylist.revoke("revoked_jti:victim", ttl_seconds=60)

    assert denylist.is_revoked("victim") is False


def test_an_empty_jti_does_not_revoke_every_other_jti(denylist):
    denylist.revoke("", ttl_seconds=60)

    assert denylist.is_revoked("jti-1") is False


# --- fail-open: an unreachable Redis must never break auth ---------------------


class _AlwaysBrokenClient:
    """Stands in for a `redis.Redis` whose connection is down."""

    def exists(self, *_args, **_kwargs):
        raise redis.ConnectionError("simulated outage")

    def set(self, *_args, **_kwargs):
        raise redis.ConnectionError("simulated outage")


@pytest.fixture
def broken_denylist() -> RedisTokenDenylist:
    return RedisTokenDenylist(_AlwaysBrokenClient())


def test_is_revoked_fails_open_when_redis_is_unreachable(broken_denylist):
    """The explicit security-vs-availability call this module makes: an outage must
    not turn into "every authenticated request in the app is rejected"."""
    assert broken_denylist.is_revoked("jti-1") is False


def test_revoke_swallows_a_redis_outage_instead_of_raising(broken_denylist):
    """A failed revoke must not turn POST /auth/logout's 204 into a 500."""
    broken_denylist.revoke("jti-1", ttl_seconds=60)  # must not raise


def test_is_revoked_logs_the_outage_as_a_warning(broken_denylist, caplog):
    with caplog.at_level(logging.WARNING, logger="app.core.revocation"):
        broken_denylist.is_revoked("jti-1")

    assert "token_denylist_unreachable" in caplog.text


def test_revoke_logs_the_outage_as_an_error(broken_denylist, caplog):
    """A failed *revoke* is logged more loudly than a failed check: it is the exact
    gap this feature exists to close, silently reopening."""
    with caplog.at_level(logging.ERROR, logger="app.core.revocation"):
        broken_denylist.revoke("jti-1", ttl_seconds=60)

    assert "token_denylist_revoke_failed" in caplog.text


# --- from_url: must not connect at construction time ----------------------------


def test_from_url_does_not_connect_eagerly():
    """redis-py connects lazily; building the denylist must never touch the network
    at construction time, so `uv run uvicorn` boots without a reachable Redis (see
    the module docstring)."""
    denylist = RedisTokenDenylist.from_url("redis://localhost:1/0")

    assert isinstance(denylist, RedisTokenDenylist)


def test_from_url_built_denylist_still_fails_open_on_first_use():
    denylist = RedisTokenDenylist.from_url("redis://localhost:1/0")

    assert denylist.is_revoked("jti-1") is False


def test_from_url_built_denylist_swallows_a_revoke_against_a_dead_address():
    """The write side of the same path: a real (not stubbed) connection failure must
    not raise out of `revoke` either."""
    denylist = RedisTokenDenylist.from_url("redis://localhost:1/0")

    denylist.revoke("jti-1", ttl_seconds=60)  # must not raise


def test_timeouts_fail_open_the_same_way_as_refused_connections(caplog):
    """`redis.TimeoutError` is a distinct class from `ConnectionError` -- a slow Redis
    must fail open exactly like an unreachable one, not surface as a 500."""

    class _TimingOutClient:
        def exists(self, *_args, **_kwargs):
            raise redis.TimeoutError("simulated slow redis")

        def set(self, *_args, **_kwargs):
            raise redis.TimeoutError("simulated slow redis")

    denylist = RedisTokenDenylist(_TimingOutClient())

    with caplog.at_level(logging.WARNING, logger="app.core.revocation"):
        assert denylist.is_revoked("jti-1") is False
    denylist.revoke("jti-1", ttl_seconds=60)  # must not raise
