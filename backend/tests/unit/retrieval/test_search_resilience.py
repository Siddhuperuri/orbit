"""Search under adverse conditions: rate limits, a cold or broken cache, and
a dead embedding provider.

The degradation these cover is the point of ADR-0024. What they assert, in
one sentence each: a search costs a metered budget, a cache hit costs no
provider call, a cache outage costs only latency, and an AI outage costs the
semantic half of the ranking and nothing else.
"""

from __future__ import annotations

import base64
import dataclasses
import uuid
from array import array
from collections.abc import Sequence

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.retrieval.hybrid_search import (
    SEMANTIC_UNAVAILABLE,
    HybridSearch,
    SearchPolicy,
)
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import AIProviderUnavailableError, RateLimitedError
from orbit.domain.ports.audit import AuditEvent
from orbit.domain.ports.rate_limiter import RateLimitDecision, RateLimitRule
from orbit.domain.retrieval import RetrievalMethod, RetrievalMode, SearchQuery
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.cache.query_vectors import RedisQueryVectorCache
from tests.conftest import build_settings
from tests.unit.processing.harness import Pipeline, build_pipeline

HANDBOOK = "Parental leave is sixteen weeks at full pay, booked through the HR portal. " * 3


@dataclasses.dataclass
class CountingEmbedder:
    """The fake provider, counting how often a query actually reaches it."""

    inner: FakeEmbeddingProvider = dataclasses.field(
        default_factory=lambda: FakeEmbeddingProvider(dimensions=32)
    )
    failure: BaseException | None = None
    query_calls: int = 0

    @property
    def space(self) -> EmbeddingSpace:
        return self.inner.space

    @property
    def max_batch_size(self) -> int:
        return self.inner.max_batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self.inner.max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self.inner.embed_documents(texts)

    async def embed_query(self, text: str) -> Sequence[float]:
        self.query_calls += 1
        if self.failure is not None:
            raise self.failure
        return await self.inner.embed_query(text)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value


class BrokenRedis:
    async def get(self, key: str) -> str | None:
        raise RedisConnectionError("Redis is down")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        raise RedisConnectionError("Redis is down")


class CountingLimiter:
    """A limiter that allows the first `allow` checks and then rejects."""

    def __init__(self, allow: int = 1_000) -> None:
        self.allow = allow
        self.keys: list[str] = []
        self.resets: list[str] = []

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        self.keys.append(key)
        allowed = len(self.keys) <= self.allow
        return RateLimitDecision(allowed=allowed, remaining=0, retry_after_seconds=42)

    async def reset(self, key: str) -> None:
        self.resets.append(key)


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


async def build_search(
    *,
    limiter: CountingLimiter | None = None,
    redis: object | None = None,
    embedder: CountingEmbedder | None = None,
) -> tuple[Pipeline, HybridSearch, CountingEmbedder, RecordingAudit]:
    pipeline = await build_pipeline()
    await pipeline.upload("handbook.txt", HANDBOOK.encode())
    await pipeline.drain()

    embedder = embedder or CountingEmbedder()
    audit = RecordingAudit()
    guard = (
        AuthRateLimitGuard(limiter, RateLimitPolicy.for_search(build_settings()), audit)
        if limiter is not None
        else None
    )
    cache = (
        RedisQueryVectorCache(redis, ttl_seconds=60)  # type: ignore[arg-type]
        if redis is not None
        else None
    )
    search = HybridSearch(
        pipeline.uow_factory,
        embedder,
        SearchPolicy(ef_search=40),
        vector_cache=cache,
        rate_limit=guard,
    )
    return pipeline, search, embedder, audit


class TestRateLimiting:
    async def test_a_search_is_charged_against_the_account_and_the_address(self) -> None:
        limiter = CountingLimiter()
        pipeline, search, _, _ = await build_search(limiter=limiter)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"), client_ip="10.0.0.9")

        assert [key.split(":")[1] for key in limiter.keys] == ["account", "ip"]
        assert all(key.startswith("search:") for key in limiter.keys)
        # The account key is a hash of the user id, never the id itself.
        assert str(pipeline.ctx.user_id) not in limiter.keys[0]
        assert limiter.keys[1].endswith(":10.0.0.9")

    async def test_exceeding_the_limit_rejects_before_any_provider_call(self) -> None:
        """The limit exists to stop the cost, so it has to gate entry."""
        limiter = CountingLimiter(allow=0)
        pipeline, search, embedder, _ = await build_search(limiter=limiter)

        with pytest.raises(RateLimitedError) as rejected:
            await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert rejected.value.retry_after_seconds == 42
        assert embedder.query_calls == 0

    async def test_a_rejection_is_audited_under_the_search_scope(self) -> None:
        limiter = CountingLimiter(allow=0)
        pipeline, search, _, audit = await build_search(limiter=limiter)

        with pytest.raises(RateLimitedError):
            await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        (event,) = audit.events
        assert event.metadata["scope"] == "search"

    async def test_an_internal_search_is_not_charged_twice(self) -> None:
        """`AnswerQuestion` has already spent the stricter `chat` budget.

        Charging the search budget as well would mean a user asking questions
        gradually loses the ability to search -- two costs for one request.
        """
        limiter = CountingLimiter()
        pipeline, search, _, _ = await build_search(limiter=limiter)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"), metered=False)

        assert limiter.keys == []

    async def test_metering_is_on_unless_a_caller_opts_out(self) -> None:
        """A route added later cannot skip the limit by forgetting an argument."""
        limiter = CountingLimiter()
        pipeline, search, _, _ = await build_search(limiter=limiter)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert limiter.keys != []


class TestQueryVectorCache:
    async def test_a_repeated_query_costs_one_provider_call(self) -> None:
        redis = FakeRedis()
        pipeline, search, embedder, _ = await build_search(redis=redis)

        first = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))
        second = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert embedder.query_calls == 1
        # The cached path is not a degraded path: the ranking is identical.
        assert [r.chunk.id for r in first.results] == [r.chunk.id for r in second.results]
        assert second.retrievers == (RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC)

    async def test_another_tenant_does_not_ride_on_the_cached_vector(self) -> None:
        """The key is workspace-scoped, so a second tenant pays its own call.

        This is the side-channel control from ADR-0024, asserted at the level
        a reader of `HybridSearch` cares about rather than at the adapter's.
        """
        redis = FakeRedis()
        pipeline, search, embedder, _ = await build_search(redis=redis)
        other = dataclasses.replace(pipeline.ctx, workspace_id=uuid.uuid4())

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))
        await search.execute(other, SearchQuery(text="parental leave"))

        assert embedder.query_calls == 2

    async def test_the_cached_vector_is_the_one_the_provider_returned(self) -> None:
        redis = FakeRedis()
        pipeline, search, embedder, _ = await build_search(redis=redis)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        expected = array("f", await embedder.inner.embed_query("parental leave"))
        (stored,) = redis.store.values()
        assert array("f", base64.b64decode(stored)) == expected

    async def test_a_redis_outage_costs_latency_and_nothing_else(self) -> None:
        """Fail-open: every search falls through to the provider, and the
        results are exactly what an uncached instance would return."""
        pipeline, search, embedder, _ = await build_search(redis=BrokenRedis())

        first = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))
        second = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert embedder.query_calls == 2
        assert first.degraded is None and second.degraded is None
        assert [r.chunk.id for r in first.results] == [r.chunk.id for r in second.results]

    async def test_a_lexical_only_search_never_touches_the_cache(self) -> None:
        redis = FakeRedis()
        pipeline, search, _, _ = await build_search(redis=redis)

        await search.execute(
            pipeline.ctx, SearchQuery(text="parental leave", mode=RetrievalMode.LEXICAL)
        )

        assert redis.store == {}


class TestProviderOutage:
    async def test_search_still_works_when_the_ai_provider_is_down(self) -> None:
        """The headline degradation: AI unavailable, search unaffected except
        for losing its semantic half, and saying so."""
        msg = "provider down"
        embedder = CountingEmbedder(failure=AIProviderUnavailableError(msg))
        pipeline, search, _, _ = await build_search(
            redis=FakeRedis(), limiter=CountingLimiter(), embedder=embedder
        )

        response = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert response.degraded == SEMANTIC_UNAVAILABLE
        assert response.retrievers == (RetrievalMethod.LEXICAL,)
        assert response.results

    async def test_an_outage_writes_nothing_to_the_cache(self) -> None:
        """A failed call must not poison the cache with an empty or partial
        vector that would outlive the outage."""
        msg = "provider down"
        redis = FakeRedis()
        embedder = CountingEmbedder(failure=AIProviderUnavailableError(msg))
        pipeline, search, _, _ = await build_search(redis=redis, embedder=embedder)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert redis.store == {}

    async def test_a_cached_vector_survives_the_outage_that_follows_it(self) -> None:
        """A query embedded before the provider died keeps its semantic half:
        the cache is a genuine buffer against a short outage, not only a cost
        saving."""
        redis = FakeRedis()
        embedder = CountingEmbedder()
        pipeline, search, _, _ = await build_search(redis=redis, embedder=embedder)

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))
        msg = "provider down"
        embedder.failure = AIProviderUnavailableError(msg)
        during_outage = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert during_outage.degraded is None
        assert during_outage.retrievers == (RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC)
