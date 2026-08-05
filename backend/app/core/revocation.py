"""Token revocation (JWT denylist) for ``POST /auth/logout``.

Every JWT this app issues (``app/core/security.py``) carries a ``jti`` claim.
Logging out revokes that specific ``jti`` here; ``app/api/deps.py``'s
``get_session_context`` checks it on every authenticated request, so a token
presented after its owner logged out reads exactly like an expired/invalid one
(401 ``not_authenticated``) — it does not leak "this token was explicitly
revoked" as a distinguishable state.

Scoped narrowly to this one gap (the mvp-implementation-sequencing checkpoint's
ruling A3, and the project's stated preference against speculative
abstraction): this is not a general cache layer, and nothing here is used for
any purpose other than logout revocation.

**Fail-open, by design.** ``RedisTokenDenylist.is_revoked`` treats an
unreachable Redis as "not revoked" rather than rejecting every authenticated
request in the app. The alternative (fail closed) would make Redis — introduced
here for the first time, for a feature that only matters once a token has
already been explicitly logged out — a hard dependency for the *entire*
authenticated API surface: a Redis blip would turn into a total auth outage
even for users who never logged out at all. Fail-open keeps the blast radius of
a Redis outage limited to exactly the residual risk ruling A3 already accepted
for every token before this feature existed (a leaked-but-not-yet-logged-out
token stays valid for up to ``JWT_EXPIRE_MINUTES``) — it does not widen that
risk, it just stops closing it *temporarily*, during an outage, for tokens
whose owner has since logged out. ``revoke`` fails open the same way on the
write side: a failed logout-time revoke is logged loudly (this *is* the gap
the feature exists to close) but must never turn a 204 logout into a 500 —
mirrors ``NotificationPort``'s "must not raise into the caller" contract for
the same underlying reason (README Design Decisions §8: a side-effect failure
must never fail the operation that triggered it).
"""

import logging
from typing import Protocol

import redis

logger = logging.getLogger(__name__)

_REVOKED_KEY_PREFIX = "revoked_jti:"


class TokenDenylist(Protocol):
    """Interface the rest of the app depends on. ``app/api/deps.py`` and
    ``app/api/auth.py`` talk to this, never to a Redis client directly, so
    swapping the backing store later touches only this module's binding."""

    def is_revoked(self, jti: str) -> bool: ...

    def revoke(self, jti: str, ttl_seconds: int) -> None: ...


class RedisTokenDenylist:
    """``SET ... EX``-backed denylist: a revoked jti's key expires naturally at the
    token's original ``exp``, so the denylist can never grow unbounded — there
    is no cleanup job, because Redis's own TTL *is* the cleanup.
    """

    def __init__(self, client: "redis.Redis") -> None:
        self._client = client

    @classmethod
    def from_url(cls, redis_url: str) -> "RedisTokenDenylist":
        # redis-py connects lazily on the first command, so building this at
        # app startup never blocks or fails even when Redis is unreachable —
        # load-bearing for `uv run uvicorn` without Docker (see module
        # docstring: this must not become a hard boot-time dependency).
        return cls(redis.Redis.from_url(redis_url, decode_responses=True))

    def is_revoked(self, jti: str) -> bool:
        try:
            return bool(self._client.exists(_REVOKED_KEY_PREFIX + jti))
        except redis.RedisError:
            logger.warning(
                "token_denylist_unreachable",
                extra={"operation": "is_revoked", "jti": jti},
            )
            return False  # fail open — see module docstring

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return  # already expired: nothing left to protect
        try:
            # SET ... EX rather than the deprecated SETEX alias.
            self._client.set(_REVOKED_KEY_PREFIX + jti, "1", ex=ttl_seconds)
        except redis.RedisError:
            logger.error(
                "token_denylist_revoke_failed",
                extra={"operation": "revoke", "jti": jti, "ttl_seconds": ttl_seconds},
            )
            # Swallowed deliberately: a failed revoke must not turn logout's 204
            # into a 500. The client-side cookie/token is still discarded either
            # way; only the server-side denylist entry is missing until Redis
            # recovers, which is the same residual exposure fail-open accepts
            # on the read side (see module docstring).
