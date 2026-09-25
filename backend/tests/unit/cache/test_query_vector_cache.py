"""The query-vector cache: tenant scoping, space scoping, and fail-open.

These are the three properties that make the cache safe to have at all. The
first two are authorization and correctness controls (ADR-0024); the third is
what keeps a Redis outage a latency event rather than a search outage.
"""

from __future__ import annotations

import base64
import uuid
from array import array

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from orbit.domain.embeddings import EmbeddingSpace
from orbit.infrastructure.cache.query_vectors import (
    NullQueryVectorCache,
    RedisQueryVectorCache,
)

SPACE = EmbeddingSpace(model="test-embed", dimensions=4)
OTHER_SPACE = EmbeddingSpace(model="test-embed-large", dimensions=4)
VECTOR = [0.25, -0.5, 0.75, 1.0]
QUERY = "quarterly revenue"


class FakeRedis:
    """The two commands the cache uses, over a dict."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.ttls[key] = ttl


class BrokenRedis:
    """Every command fails, as it does while Redis is unreachable."""

    async def get(self, key: str) -> str | None:
        raise RedisConnectionError("Redis is down")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        raise RedisConnectionError("Redis is down")


def cache(redis: object, *, ttl: int = 60) -> RedisQueryVectorCache:
    return RedisQueryVectorCache(redis, ttl_seconds=ttl)  # type: ignore[arg-type]


async def test_round_trips_a_vector() -> None:
    workspace = uuid.uuid4()
    subject = cache(FakeRedis())

    assert await subject.get(workspace_id=workspace, space=SPACE, text=QUERY) is None
    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)

    stored = await subject.get(workspace_id=workspace, space=SPACE, text=QUERY)
    assert stored == array("f", VECTOR)


async def test_a_workspaces_entry_is_invisible_to_another_workspace() -> None:
    """The tenant scope, which is the whole reason the key carries a workspace.

    Without it, two tenants share entries, and the latency difference between
    a hit and a miss tells one of them what the other searched for.
    """
    redis = FakeRedis()
    subject = cache(redis)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    await subject.put(workspace_id=tenant_a, space=SPACE, text=QUERY, vector=VECTOR)

    assert await subject.get(workspace_id=tenant_b, space=SPACE, text=QUERY) is None
    # And the two writes coexist rather than overwriting each other.
    await subject.put(workspace_id=tenant_b, space=SPACE, text=QUERY, vector=VECTOR)
    assert len(redis.store) == 2


async def test_changing_the_embedding_space_invalidates_without_a_flush() -> None:
    """A vector from one model must never answer a query against another.

    The space is part of the key, so switching models is self-invalidating:
    no operator step, no flush, and no window in which the old space's vectors
    are fused into the new space's ranking.
    """
    subject = cache(FakeRedis())
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)

    assert await subject.get(workspace_id=workspace, space=OTHER_SPACE, text=QUERY) is None


async def test_distinct_queries_do_not_share_an_entry() -> None:
    subject = cache(FakeRedis())
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)

    assert await subject.get(workspace_id=workspace, space=SPACE, text="something else") is None


async def test_the_query_text_is_not_recoverable_from_the_key() -> None:
    """Keys reach `SLOWLOG`, `MONITOR`, and keyspace dumps. A cache of raw
    search phrases would make all three a readable log of what tenants look
    for."""
    redis = FakeRedis()
    subject = cache(redis)

    await subject.put(
        workspace_id=uuid.uuid4(), space=SPACE, text="salary band for Jane", vector=VECTOR
    )

    key = next(iter(redis.store))
    assert "salary" not in key
    assert "Jane" not in key


async def test_reads_fail_open_when_redis_is_down() -> None:
    """An unreachable cache is a miss, which every caller already handles."""
    subject = cache(BrokenRedis())

    assert await subject.get(workspace_id=uuid.uuid4(), space=SPACE, text=QUERY) is None


async def test_writes_fail_open_when_redis_is_down() -> None:
    subject = cache(BrokenRedis())

    # The absence of a raised exception is the assertion: a failed cache write
    # must never fail the search that produced the value.
    await subject.put(workspace_id=uuid.uuid4(), space=SPACE, text=QUERY, vector=VECTOR)


async def test_a_value_of_the_wrong_width_is_a_miss() -> None:
    """Defensive: the space is in the key, so this can only be a truncated
    write or a foreign writer -- neither of which may enter a ranking."""
    redis = FakeRedis()
    subject = cache(redis)
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)
    key = next(iter(redis.store))
    redis.store[key] = base64.b64encode(array("f", [1.0, 2.0]).tobytes()).decode("ascii")

    assert await subject.get(workspace_id=workspace, space=SPACE, text=QUERY) is None


async def test_an_undecodable_value_is_a_miss() -> None:
    redis = FakeRedis()
    subject = cache(redis)
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)
    redis.store[next(iter(redis.store))] = "not base64 at all!!"

    assert await subject.get(workspace_id=workspace, space=SPACE, text=QUERY) is None


async def test_the_configured_ttl_is_applied() -> None:
    redis = FakeRedis()
    subject = cache(redis, ttl=900)

    await subject.put(workspace_id=uuid.uuid4(), space=SPACE, text=QUERY, vector=VECTOR)

    assert set(redis.ttls.values()) == {900}


@pytest.mark.parametrize("vector", [VECTOR, array("f", VECTOR)])
async def test_accepts_either_a_list_or_a_float32_array(vector: object) -> None:
    """`to_indexable_vector` hands over an `array('f')`; tests and future
    callers may hand over a plain sequence."""
    subject = cache(FakeRedis())
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=vector)  # type: ignore[arg-type]

    assert await subject.get(workspace_id=workspace, space=SPACE, text=QUERY) == array("f", VECTOR)


async def test_the_null_cache_never_stores_anything() -> None:
    subject = NullQueryVectorCache()
    workspace = uuid.uuid4()

    await subject.put(workspace_id=workspace, space=SPACE, text=QUERY, vector=VECTOR)

    assert await subject.get(workspace_id=workspace, space=SPACE, text=QUERY) is None
