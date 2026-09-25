"""Redis-backed rate limiter.

A fixed window, not a sliding one. The trade is explicit: a fixed window lets
an attacker send up to `2 * limit` attempts across a window boundary, whereas
a sliding window would not. That burst is acceptable here because the limits
are small in absolute terms (a handful of attempts), and it buys an
implementation that is one round trip, has O(1) memory per key, and is
obviously correct -- where a sliding-log implementation stores every attempt
timestamp and a sliding-window-counter needs two keys and interpolation.

The counter lives in Redis rather than PostgreSQL because a brute-force run is
high-volume, worthless, and short-lived: precisely the data that must not
compete for durable write throughput with real traffic. Redis being
disposable (docs/architecture/system.md) is also why the failure mode below
matters.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from orbit.core.logging import get_logger
from orbit.core.metrics import RATE_LIMIT_DECISIONS
from orbit.domain.ports.rate_limiter import RateLimitDecision, RateLimitRule

logger = get_logger(__name__)

_KEY_PREFIX = "orbit:ratelimit:"


class RedisRateLimiter:
    """Counts attempts per key in a fixed window.

    INCR-then-EXPIRE in one pipeline: `INCR` creates the key at 1 if absent,
    and `EXPIRE` is set unconditionally rather than only on creation. Setting
    it every time costs nothing and removes the failure mode where a crash
    between the two commands leaves a counter with no TTL -- which would lock
    an account out permanently.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        namespaced = f"{_KEY_PREFIX}{key}"
        try:
            pipeline = self._redis.pipeline()
            pipeline.incr(namespaced)
            pipeline.expire(namespaced, rule.window_seconds)
            pipeline.ttl(namespaced)
            count, _, ttl = await pipeline.execute()
        except (RedisError, OSError):
            # Fail **open**, loudly.
            #
            # This is the uncomfortable choice and it is deliberate. Failing
            # closed would turn a Redis outage into a total authentication
            # outage -- nobody can log in, including the operators trying to
            # fix it. Failing open degrades brute-force protection for the
            # duration of the outage, which is a smaller, bounded harm than
            # locking every user out of the product.
            #
            # It is only defensible because it is *visible*: this logs at
            # ERROR, and `/readyz` already reports Redis down, so the window
            # is observable rather than silent. Password hashing (Argon2id)
            # remains the cost that makes each attempt expensive regardless.
            scope = key.split(":", maxsplit=1)[0]
            # A counter, not only a log line: while this is nonzero,
            # brute-force protection is absent, and that is an alert.
            RATE_LIMIT_DECISIONS.labels(scope=scope, decision="unavailable").inc()
            logger.exception("ratelimit.backend_unavailable", key_prefix=scope)
            return RateLimitDecision(allowed=True, remaining=rule.limit, retry_after_seconds=0)

        attempts = int(count)
        # A negative TTL means "no expiry set" (-1) or "key gone" (-2); neither
        # should be reported to a client as a retry delay.
        retry_after = int(ttl) if int(ttl) > 0 else rule.window_seconds

        allowed = attempts <= rule.limit
        RATE_LIMIT_DECISIONS.labels(
            scope=key.split(":", maxsplit=1)[0], decision="allowed" if allowed else "rejected"
        ).inc()
        return RateLimitDecision(
            allowed=allowed,
            remaining=max(0, rule.limit - attempts),
            retry_after_seconds=retry_after,
        )

    async def reset(self, key: str) -> None:
        try:
            await self._redis.delete(f"{_KEY_PREFIX}{key}")
        except (RedisError, OSError):
            # Non-fatal: the counter simply expires on its own schedule. The
            # only cost is that a user who mistyped their password before
            # succeeding keeps those attempts on their tally for the rest of
            # the window.
            logger.warning("ratelimit.reset_failed")
