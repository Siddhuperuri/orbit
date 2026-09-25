"""Metrics on the read path: search, answers, caches, rate limiting, storage."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
from moto import mock_aws
from redis.exceptions import ConnectionError as RedisConnectionError

from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import (
    AIProviderUnavailableError,
    GenerationFailedError,
    RateLimitedError,
    StorageUnavailableError,
)
from orbit.domain.ports.rate_limiter import RateLimitRule
from orbit.domain.retrieval import RetrievalMode, SearchQuery
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider
from orbit.infrastructure.cache.query_vectors import RedisQueryVectorCache
from orbit.infrastructure.cache.rate_limiter import RedisRateLimiter
from orbit.infrastructure.storage.s3 import ObjectStorageClient
from tests.conftest import build_settings
from tests.unit.answering.test_answer_question import _harness
from tests.unit.fakes.scripted_llm import ScriptedLLM
from tests.unit.observability.helpers import sample
from tests.unit.retrieval.test_search_resilience import (
    BrokenRedis,
    CountingEmbedder,
    CountingLimiter,
    FakeRedis,
    build_search,
)


def _stage(stage: str) -> float:
    return sample("orbit_search_stage_duration_seconds_count", stage=stage)


def _search_total(mode: str, outcome: str) -> float:
    return sample("orbit_search_duration_seconds_count", mode=mode, outcome=outcome)


class TestSearch:
    async def test_a_hybrid_search_times_the_whole_and_each_stage(self) -> None:
        stages = ("query_embedding", "lexical", "semantic", "hydrate")
        before = {stage: _stage(stage) for stage in stages}
        total = _search_total("hybrid", "ok")
        pipeline, search, _, _ = await build_search()

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert _search_total("hybrid", "ok") == total + 1
        for stage in stages:
            assert _stage(stage) == before[stage] + 1, stage

    async def test_a_lexical_search_never_touches_the_semantic_stages(self) -> None:
        semantic, embedding = _stage("semantic"), _stage("query_embedding")
        pipeline, search, _, _ = await build_search()

        await search.execute(
            pipeline.ctx, SearchQuery(text="parental leave", mode=RetrievalMode.LEXICAL)
        )

        assert (_stage("semantic"), _stage("query_embedding")) == (semantic, embedding)

    async def test_a_provider_outage_is_counted_as_degraded_not_failed(self) -> None:
        degraded = sample("orbit_search_degraded_total", reason="semantic_unavailable")
        total = _search_total("hybrid", "degraded")
        pipeline, search, _, _ = await build_search(
            embedder=CountingEmbedder(failure=AIProviderUnavailableError("down"))
        )

        await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert sample("orbit_search_degraded_total", reason="semantic_unavailable") == degraded + 1
        assert _search_total("hybrid", "degraded") == total + 1

    async def test_a_rate_limited_search_is_a_rejection_not_an_error(self) -> None:
        """An abusive client must not look like an outage."""
        rejected, errored = _search_total("hybrid", "rejected"), _search_total("hybrid", "error")
        pipeline, search, _, _ = await build_search(limiter=CountingLimiter(allow=0))

        with pytest.raises(RateLimitedError):
            await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        assert _search_total("hybrid", "rejected") == rejected + 1
        assert _search_total("hybrid", "error") == errored


class TestAnswers:
    async def test_a_grounded_answer_is_counted_timed_and_its_citations_resolved(self) -> None:
        labels = {"status": "complete", "stop_reason": "completed", "grounding": "grounded"}
        answers = sample("orbit_rag_answers_total", **labels)
        totals = sample("orbit_rag_duration_seconds_count", stage="total")
        resolved = sample("orbit_citations_total", result="resolved")
        h = await _harness()

        message = await h.ask()

        assert sample("orbit_rag_answers_total", **labels) == answers + 1
        assert sample("orbit_rag_duration_seconds_count", stage="total") == totals + 1
        assert sample("orbit_citations_total", result="resolved") == resolved + len(
            message.citations
        )

    async def test_discarded_citations_are_counted(self) -> None:
        """ADR-0006: the signal that the model, prompt, or handle format regressed."""
        discarded = sample("orbit_citations_total", result="discarded")
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider(fabricate_citations=True)))

        message = await h.ask()

        assert message.discarded_citation_count == 3
        assert sample("orbit_citations_total", result="discarded") == discarded + 3

    async def test_a_failed_answer_is_counted_with_its_stop_reason(self) -> None:
        labels = {"status": "failed", "stop_reason": "provider_error", "grounding": "none"}
        before = sample("orbit_rag_answers_total", **labels)
        h = await _harness(llm=ScriptedLLM(failure=AIProviderUnavailableError("503")))

        with pytest.raises(GenerationFailedError):
            await h.ask()

        assert sample("orbit_rag_answers_total", **labels) == before + 1


class TestQueryVectorCache:
    SPACE = EmbeddingSpace(model="test-embed", dimensions=4)

    @staticmethod
    def _result(result: str) -> float:
        return sample("orbit_cache_requests_total", cache="query_vector", result=result)

    async def test_miss_then_hit(self) -> None:
        cache = RedisQueryVectorCache(FakeRedis(), ttl_seconds=60)  # type: ignore[arg-type]
        workspace = uuid.uuid4()
        miss, hit = self._result("miss"), self._result("hit")

        assert await cache.get(workspace_id=workspace, space=self.SPACE, text="q") is None
        await cache.put(workspace_id=workspace, space=self.SPACE, text="q", vector=[1, 2, 3, 4])
        assert await cache.get(workspace_id=workspace, space=self.SPACE, text="q") is not None

        assert (self._result("miss"), self._result("hit")) == (miss + 1, hit + 1)

    async def test_an_unreachable_redis_is_an_error_not_a_miss(self) -> None:
        """A hit-ratio dashboard must not read an outage as a cold cache."""
        cache = RedisQueryVectorCache(BrokenRedis(), ttl_seconds=60)  # type: ignore[arg-type]
        errors, misses = self._result("error"), self._result("miss")

        assert await cache.get(workspace_id=uuid.uuid4(), space=self.SPACE, text="q") is None

        assert self._result("error") == errors + 1
        assert self._result("miss") == misses


class _Pipeline:
    def __init__(self, redis: _RateRedis) -> None:
        self._redis = redis

    def incr(self, key: str) -> None: ...
    def expire(self, key: str, seconds: int) -> None: ...
    def ttl(self, key: str) -> None: ...

    async def execute(self) -> list[int]:
        if self._redis.broken:
            raise RedisConnectionError("down")
        self._redis.count += 1
        return [self._redis.count, 1, 30]


class _RateRedis:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken, self.count = broken, 0

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)


class TestRateLimiter:
    RULE = RateLimitRule(limit=1, window_seconds=60)

    @staticmethod
    def _decision(decision: str) -> float:
        return sample("orbit_rate_limit_decisions_total", scope="login", decision=decision)

    async def test_allowed_then_rejected(self) -> None:
        limiter = RedisRateLimiter(_RateRedis())  # type: ignore[arg-type]
        allowed, rejected = self._decision("allowed"), self._decision("rejected")

        assert (await limiter.check("login:account:x", self.RULE)).allowed
        assert not (await limiter.check("login:account:x", self.RULE)).allowed

        assert (self._decision("allowed"), self._decision("rejected")) == (
            allowed + 1,
            rejected + 1,
        )

    async def test_failing_open_is_counted_because_protection_is_absent_meanwhile(self) -> None:
        limiter = RedisRateLimiter(_RateRedis(broken=True))  # type: ignore[arg-type]
        before = self._decision("unavailable")

        assert (await limiter.check("login:account:x", self.RULE)).allowed

        assert self._decision("unavailable") == before + 1


class TestStorage:
    @pytest.fixture
    def storage(self) -> Iterator[ObjectStorageClient]:
        settings = build_settings(
            s3_bucket="metrics-bucket",
            s3_region="us-east-1",
            s3_endpoint_url=None,
            s3_force_path_style=False,
        )
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="metrics-bucket")
            yield ObjectStorageClient(settings)

    @staticmethod
    def _op(operation: str, outcome: str) -> float:
        return sample(
            "orbit_storage_operation_duration_seconds_count", operation=operation, outcome=outcome
        )

    async def test_each_s3_call_is_timed_separately_from_the_clients_upload(
        self, storage: ObjectStorageClient
    ) -> None:
        put, head = self._op("put_object", "ok"), self._op("head_object", "not_found")

        async def body() -> AsyncIterator[bytes]:
            yield b"data"

        await storage.put_stream("k", body(), content_type="text/plain")

        # The existence check that precedes a write is a 404 -- not an error.
        assert self._op("head_object", "not_found") == head + 1
        assert self._op("put_object", "ok") == put + 1

    async def test_a_missing_bucket_is_an_error_outcome(self, storage: ObjectStorageClient) -> None:
        broken = ObjectStorageClient(
            build_settings(s3_bucket="no-such-bucket", s3_endpoint_url=None)
        )
        before = self._op("head_bucket", "error")

        with pytest.raises(StorageUnavailableError):
            broken.head_bucket()

        assert self._op("head_bucket", "error") == before + 1
