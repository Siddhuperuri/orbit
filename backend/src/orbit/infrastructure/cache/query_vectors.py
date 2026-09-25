"""Redis-backed query-vector cache, and the no-op used when it is disabled.

Implements :class:`orbit.domain.ports.cache.QueryVectorCache`. The interesting
decisions here are the key and the failure behaviour; the storage itself is a
`GET`/`SETEX` pair.

**The key.** ``orbit:qvec:{workspace}:{model}@{dims}:{sha256(text)}``

Each component is load-bearing:

* `workspace` -- the tenant scope. Argued in the port: without it, latency
  discloses that *somebody else* searched this phrase.
* `model@dimensions` -- the embedding space. Two models' vectors are not
  comparable, so a key that omitted the space would serve a
  `text-embedding-3-small` vector to a query searching a `-3-large` index and
  produce a ranking from meaningless distances. Changing the model therefore
  also invalidates every entry, for free, without a flush.
* `sha256(text)` -- the query itself, hashed rather than embedded. Redis keys
  surface in `SLOWLOG`, `MONITOR`, and any keyspace dump; a cache of raw
  search phrases would turn those into a readable log of what every tenant is
  looking for. The same reasoning as `application/auth/rate_limits.py`.

**The value** is the float32 vector, base64-encoded. The shared Redis client
runs with `decode_responses=True` (every other user of it stores text), so the
bytes are encoded rather than stored raw. Base64 costs a third more space than
the raw buffer and avoids a second connection pool configured differently from
the first.

**Failure behaviour is fail-open, and unlike the rate limiter that requires no
argument**: a cache that cannot be read is a cache miss, which is a state the
caller already handles on every cold key. A Redis outage degrades search to
its uncached latency and cost. Nothing is wrong, only slower.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from array import array
from collections.abc import Sequence

from redis.asyncio import Redis
from redis.exceptions import RedisError

from orbit.core.logging import get_logger
from orbit.core.metrics import CACHE_REQUESTS
from orbit.domain.embeddings import EmbeddingSpace

logger = get_logger(__name__)

_KEY_PREFIX = "orbit:qvec:"

#: The `cache` label on `orbit_cache_requests_total`.
_CACHE_NAME = "query_vector"

#: The struct code pgvector's adapter and `to_indexable_vector` both use.
_FLOAT32 = "f"
_FLOAT32_BYTES = 4


class NullQueryVectorCache:
    """Every read misses, every write is discarded.

    Bound when caching is switched off, so "disabled" is a different object
    rather than a branch inside the real one -- there is no configuration
    under which a half-initialised Redis cache is consulted.
    """

    async def get(
        self, *, workspace_id: uuid.UUID, space: EmbeddingSpace, text: str
    ) -> array[float] | None:
        return None

    async def put(
        self,
        *,
        workspace_id: uuid.UUID,
        space: EmbeddingSpace,
        text: str,
        vector: Sequence[float],
    ) -> None:
        return None


class RedisQueryVectorCache:
    """Stores query vectors under a tenant- and space-scoped key."""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    def _key(self, workspace_id: uuid.UUID, space: EmbeddingSpace, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{_KEY_PREFIX}{workspace_id}:{space.key}:{digest}"

    async def get(
        self, *, workspace_id: uuid.UUID, space: EmbeddingSpace, text: str
    ) -> array[float] | None:
        try:
            raw = await self._redis.get(self._key(workspace_id, space, text))
        except (RedisError, OSError):
            # A miss, logged once at warning rather than exception: during an
            # outage this fires on every search, and a traceback per search
            # buries the incident it is reporting. `/readyz` already names
            # Redis as down.
            logger.warning("querycache.unavailable", call="get")
            CACHE_REQUESTS.labels(cache=_CACHE_NAME, result="error").inc()
            return None
        if raw is None:
            CACHE_REQUESTS.labels(cache=_CACHE_NAME, result="miss").inc()
            return None

        try:
            decoded = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            # Something else wrote this key, or wrote it in an older format.
            # Treated as a miss; the next write overwrites it.
            logger.warning("querycache.undecodable", space=space.key)
            CACHE_REQUESTS.labels(cache=_CACHE_NAME, result="error").inc()
            return None

        if len(decoded) != space.dimensions * _FLOAT32_BYTES:
            # A vector from a different space that happens to share this key
            # cannot occur -- the space is *in* the key -- so reaching here
            # means a truncated write or a foreign writer. Either way the
            # bytes are not this space's vector and must not enter a ranking.
            logger.warning(
                "querycache.width_mismatch",
                space=space.key,
                expected_bytes=space.dimensions * _FLOAT32_BYTES,
                actual_bytes=len(decoded),
            )
            CACHE_REQUESTS.labels(cache=_CACHE_NAME, result="error").inc()
            return None

        vector: array[float] = array(_FLOAT32)
        vector.frombytes(decoded)
        CACHE_REQUESTS.labels(cache=_CACHE_NAME, result="hit").inc()
        return vector

    async def put(
        self,
        *,
        workspace_id: uuid.UUID,
        space: EmbeddingSpace,
        text: str,
        vector: Sequence[float],
    ) -> None:
        buffer: array[float] = (
            vector
            if isinstance(vector, array) and vector.typecode == _FLOAT32
            else array(_FLOAT32, vector)
        )
        if len(buffer) != space.dimensions:  # pragma: no cover -- validated upstream
            # `to_indexable_vector` already enforced this. Refusing to store a
            # disagreeing vector keeps the invariant true for readers even if
            # a future caller skips that validation.
            logger.error("querycache.refused_width", space=space.key, dimensions=len(buffer))
            return

        payload = base64.b64encode(buffer.tobytes()).decode("ascii")
        try:
            await self._redis.setex(self._key(workspace_id, space, text), self._ttl, payload)
        except (RedisError, OSError):
            logger.warning("querycache.unavailable", call="put")
