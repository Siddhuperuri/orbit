"""What ORBIT does when a dependency is down, verified against real ones.

Every scenario here is a row in `docs/operations/failure-modes.md`. They are
integration tests rather than unit tests because the claims are about the
*boundary between two systems* -- "storage failed and PostgreSQL holds no row
for it", "Redis is gone and the job row is still there for recovery to find"
-- and a fake on either side of that boundary would let the claim pass while
the real pairing was broken.

An outage is simulated by pointing one client at a closed port. That is both
the cheapest and the most honest way: the client's own timeout, retry, and
error-translation code runs exactly as it would in production, which is the
code these tests exist to exercise. Stopping a container would prove the same
thing and take a hundred times longer.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.health.check_readiness import CheckReadiness
from orbit.application.retrieval.hybrid_search import (
    SEMANTIC_UNAVAILABLE,
    HybridSearch,
    SearchPolicy,
)
from orbit.core.config import Settings
from orbit.core.storage_keys import workspace_prefix
from orbit.domain.access import AccessContext, Role
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import AIProviderUnavailableError, StorageUnavailableError
from orbit.domain.ports.health import DependencyStatus
from orbit.domain.ports.rate_limiter import RateLimitDecision, RateLimitRule
from orbit.domain.retrieval import RetrievalMethod, SearchQuery
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.cache.query_vectors import RedisQueryVectorCache
from orbit.infrastructure.cache.rate_limiter import RedisRateLimiter
from orbit.infrastructure.cache.redis import RedisClient
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import make_unit_of_work_factory
from orbit.infrastructure.health import RedisProbe
from orbit.infrastructure.storage.s3 import ObjectStorageClient
from tests.conftest import build_settings
from tests.unit.fakes.fake_processing import RecordingJobQueue
from tests.unit.fakes.security_doubles import RecordingAuditSink

pytestmark = pytest.mark.integration

# Must match the `chunks.embedding` column the migrations build: a narrower
# space is rejected by pgvector on the first dense query, which would make
# these tests fail for a reason unrelated to the outage they describe.
SPACE = EmbeddingSpace(model="fake-embed", dimensions=1536)
HANDBOOK = b"Parental leave is sixteen weeks at full pay, booked through the HR portal. " * 20


def _closed_port() -> int:
    """A port nothing is listening on.

    Bound and immediately released, so the number is real and free rather than
    a guess that some other service might occupy on a developer's machine.
    A connection to it is refused at once -- the fastest, most deterministic
    stand-in for "this dependency is down".
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(**overrides: object) -> Settings:
    required = ("ORBIT_TEST_DATABASE_URL", "ORBIT_S3_BUCKET", "ORBIT_S3_ACCESS_KEY_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"not configured for integration: missing {', '.join(missing)}")
    defaults: dict[str, object] = {
        "database_url": os.environ["ORBIT_TEST_DATABASE_URL"],
        "redis_url": os.environ.get("ORBIT_REDIS_URL", "redis://localhost:6379/9"),
        "s3_bucket": os.environ["ORBIT_S3_BUCKET"],
        "s3_endpoint_url": os.environ.get("ORBIT_S3_ENDPOINT_URL", "http://localhost:9000"),
        "s3_access_key_id": os.environ["ORBIT_S3_ACCESS_KEY_ID"],
        "s3_secret_access_key": os.environ["ORBIT_S3_SECRET_ACCESS_KEY"],
        "embedding_dimensions": SPACE.dimensions,
        # Short, so an unreachable dependency fails inside a test's patience
        # rather than the default production window.
        "redis_connect_timeout_seconds": 0.5,
        "redis_command_timeout_seconds": 0.5,
        "s3_connect_timeout_seconds": 0.5,
        "s3_read_timeout_seconds": 1.0,
        "s3_max_attempts": 1,
    }
    defaults.update(overrides)
    return build_settings(**defaults)


@dataclass
class Tenant:
    """A real workspace, and the pieces of ORBIT that act on it."""

    settings: Settings
    engine: AsyncEngine
    database: Database
    storage: ObjectStorageClient
    ctx: AccessContext
    queue: RecordingJobQueue

    async def rows(self, sql: str, **params: object) -> list[dict[str, object]]:
        async with self.engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            return [dict(row._mapping) for row in result]

    def upload_with(self, storage: ObjectStorageClient, limiter: object) -> UploadDocument:
        """An upload use case wired to a given storage client and limiter.

        Both are parameters because each failure test substitutes exactly one
        of them for a broken equivalent, leaving every other collaborator
        real.
        """
        uow_factory = make_unit_of_work_factory(self.database, cursor_secret="integration")
        guard = AuthRateLimitGuard(
            limiter,  # type: ignore[arg-type]
            RateLimitPolicy.for_upload(self.settings),
            RecordingAuditSink(),
        )
        return UploadDocument(uow_factory, storage, self.settings, guard, self.queue)

    def search_with(self, cache: object | None) -> HybridSearch:
        uow_factory = make_unit_of_work_factory(self.database, cursor_secret="integration")
        return HybridSearch(
            uow_factory,
            FakeEmbeddingProvider(dimensions=SPACE.dimensions),
            SearchPolicy(ef_search=40),
            vector_cache=cache,  # type: ignore[arg-type]
        )


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.fixture
async def tenant(migrated_engine: AsyncEngine) -> AsyncIterator[Tenant]:
    settings = _settings()
    database = Database(settings)
    storage = ObjectStorageClient(settings)
    uow_factory = make_unit_of_work_factory(database, cursor_secret="integration")

    async with uow_factory() as uow:
        user = await uow.users.create(
            email=f"failure-{uuid.uuid4().hex[:10]}@example.test",
            password_hash="$argon2id$fake",
            full_name="Failure Modes",
        )
        workspace = await uow.workspaces.create(
            name="Failure Modes",
            slug=f"failure-{uuid.uuid4().hex[:10]}",
            created_by_user_id=user.id,
        )
        await uow.memberships.add_owner(workspace.id, user.id)
        await uow.commit()

    built = Tenant(
        settings=settings,
        engine=migrated_engine,
        database=database,
        storage=storage,
        ctx=AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER),
        queue=RecordingJobQueue(),
    )
    try:
        yield built
    finally:
        async with migrated_engine.begin() as conn:
            await conn.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": workspace.id})
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
        async for summary in storage.list_keys(workspace_prefix(workspace.id)):
            await storage.delete(summary.key)
        await database.dispose()
        storage.close()


class InMemoryLimiter:
    """Stands in for Redis where a test is not exercising the limiter."""

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=1, retry_after_seconds=0)

    async def reset(self, key: str) -> None:
        return None


# ---------------------------------------------------------------------------
# Object storage
# ---------------------------------------------------------------------------


class TestObjectStorageUnavailable:
    async def test_an_upload_fails_cleanly_and_writes_no_database_row(self, tenant: Tenant) -> None:
        """The invariant ADR-0011's ordering exists to protect.

        Storage is written before PostgreSQL precisely so that a storage
        outage leaves *nothing* rather than a document row pointing at bytes
        that were never stored. A row like that is visible to the user,
        undownloadable, and fails the pipeline forever; this test is what
        stops a future refactor reversing the order.
        """
        dead = ObjectStorageClient(_settings(s3_endpoint_url=f"http://127.0.0.1:{_closed_port()}"))
        upload = tenant.upload_with(dead, InMemoryLimiter())

        with pytest.raises(StorageUnavailableError):
            await upload.execute(
                tenant.ctx,
                filename="handbook.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(HANDBOOK),
            )

        documents = await tenant.rows(
            "SELECT id FROM documents WHERE workspace_id = :w", w=tenant.ctx.workspace_id
        )
        assert documents == []
        dead.close()

    async def test_the_failure_is_reported_as_retryable_not_as_a_bad_request(
        self, tenant: Tenant
    ) -> None:
        """A 503 tells the client to try again; a 4xx would tell them their
        file was the problem, which is a lie that costs a support ticket."""
        dead = ObjectStorageClient(_settings(s3_endpoint_url=f"http://127.0.0.1:{_closed_port()}"))
        upload = tenant.upload_with(dead, InMemoryLimiter())

        with pytest.raises(StorageUnavailableError) as failure:
            await upload.execute(
                tenant.ctx,
                filename="handbook.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(HANDBOOK),
            )

        assert failure.value.retryable is True
        assert failure.value.http_status == 503
        # The endpoint, bucket, and credentials stay inside the adapter.
        assert "127.0.0.1" not in failure.value.message
        dead.close()

    async def test_browsing_and_search_are_unaffected_while_storage_is_down(
        self, tenant: Tenant
    ) -> None:
        """Storage holds bytes; PostgreSQL holds everything a listing or a
        search reads. Losing the former must not take the latter with it."""
        working = tenant.upload_with(tenant.storage, InMemoryLimiter())
        await working.execute(
            tenant.ctx,
            filename="handbook.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(HANDBOOK),
        )

        # Storage is now irrelevant: nothing below opens an object.
        response = await tenant.search_with(None).execute(
            tenant.ctx, SearchQuery(text="parental leave")
        )

        assert response.degraded is None
        documents = await tenant.rows(
            "SELECT id FROM documents WHERE workspace_id = :w", w=tenant.ctx.workspace_id
        )
        assert len(documents) == 1


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


@pytest.fixture
async def dead_redis() -> AsyncIterator[RedisClient]:
    client = RedisClient(_settings(redis_url=f"redis://127.0.0.1:{_closed_port()}/0"))
    try:
        yield client
    finally:
        await client.close()


class TestRedisUnavailable:
    async def test_readiness_reports_redis_down_rather_than_hanging(
        self, dead_redis: RedisClient
    ) -> None:
        """The outage has to be *visible*: every other Redis behaviour below
        fails open, and fail-open is only defensible when an operator can see
        that it is happening.

        Visible, but not fatal. Redis is a dependency ORBIT is built to lose
        (ADR-0024), so it is probed as non-critical: the instance stays in
        rotation and reports `degraded`. Taking every instance out of rotation
        for it would turn a survivable outage into the total one the design
        exists to prevent.
        """
        report = await CheckReadiness([RedisProbe(dead_redis)]).execute()

        (redis_health,) = report.dependencies
        assert redis_health.status is DependencyStatus.DOWN
        assert redis_health.critical is False
        # The probe returned rather than hanging -- the point of the timeout.
        assert redis_health.detail == "unavailable"

        assert report.is_ready is True
        assert report.is_degraded is True

    async def test_rate_limiting_fails_open_rather_than_locking_everyone_out(
        self, dead_redis: RedisClient
    ) -> None:
        """Deliberate, and argued in `cache/rate_limiter.py`: failing closed
        would turn a Redis outage into a total authentication outage,
        including for the operators trying to fix it."""
        limiter = RedisRateLimiter(dead_redis.client)

        decision = await limiter.check(
            "login:account:abc", RateLimitRule(limit=1, window_seconds=60)
        )

        assert decision.allowed is True

    async def test_an_upload_still_succeeds_and_leaves_a_job_for_recovery(
        self, tenant: Tenant, dead_redis: RedisClient
    ) -> None:
        """The upload's durable half does not involve Redis at all.

        The object is stored, the document, version, and job commit in one
        transaction, and only the *publish* needs the broker. A failed publish
        delays the job until recovery re-publishes it -- so refusing the
        upload would report a failure that did not happen and throw away
        bytes the user already sent.
        """
        upload = tenant.upload_with(tenant.storage, RedisRateLimiter(dead_redis.client))

        result = await upload.execute(
            tenant.ctx,
            filename="handbook.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(HANDBOOK),
        )

        jobs = await tenant.rows(
            "SELECT j.status FROM document_processing_jobs j"
            " JOIN document_versions v ON v.id = j.document_version_id"
            " WHERE v.document_id = :d",
            d=result.document.id,
        )
        assert [job["status"] for job in jobs] == ["queued"]

    async def test_search_still_works_with_the_cache_unreachable(
        self, tenant: Tenant, dead_redis: RedisClient
    ) -> None:
        """The cache is an optimisation. Losing it costs a provider round trip
        per query and changes nothing else -- including the ranking."""
        working = tenant.upload_with(tenant.storage, InMemoryLimiter())
        await working.execute(
            tenant.ctx,
            filename="handbook.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(HANDBOOK),
        )

        cache = RedisQueryVectorCache(dead_redis.client, ttl_seconds=60)
        response = await tenant.search_with(cache).execute(
            tenant.ctx, SearchQuery(text="parental leave")
        )

        assert response.degraded is None
        assert RetrievalMethod.LEXICAL in response.retrievers


class TestQueryVectorCacheAgainstRealRedis:
    """The adapter's contract, against a real server rather than a dict.

    `tests/unit/cache/` covers the logic; this covers the pairing -- that the
    key survives round-tripping through a client configured with
    `decode_responses=True`, and that the TTL is a TTL the server honours.
    """

    @pytest.fixture
    async def redis(self) -> AsyncIterator[RedisClient]:
        client = RedisClient(_settings())
        try:
            await client.ping()
        except Exception:
            pytest.skip("Redis is not reachable; run `npm run infra:up`")
        try:
            yield client
        finally:
            await client.close()

    async def test_a_vector_survives_a_real_round_trip(self, redis: RedisClient) -> None:
        cache = RedisQueryVectorCache(redis.client, ttl_seconds=30)
        workspace = uuid.uuid4()
        vector = [0.5, -0.25, 0.125] + [0.0] * (SPACE.dimensions - 3)

        await cache.put(workspace_id=workspace, space=SPACE, text="round trip", vector=vector)
        stored = await cache.get(workspace_id=workspace, space=SPACE, text="round trip")

        assert stored is not None
        assert list(stored) == pytest.approx(vector)

    async def test_the_entry_expires(self, redis: RedisClient) -> None:
        cache = RedisQueryVectorCache(redis.client, ttl_seconds=30)
        workspace = uuid.uuid4()
        await cache.put(
            workspace_id=workspace, space=SPACE, text="ttl", vector=[0.0] * SPACE.dimensions
        )

        keys = [key async for key in redis.client.scan_iter(f"orbit:qvec:{workspace}:*")]

        assert len(keys) == 1
        assert 0 < await redis.client.ttl(keys[0]) <= 30

    async def test_one_tenants_key_is_not_another_tenants_key(self, redis: RedisClient) -> None:
        cache = RedisQueryVectorCache(redis.client, ttl_seconds=30)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

        await cache.put(
            workspace_id=tenant_a, space=SPACE, text="shared", vector=[1.0] * SPACE.dimensions
        )

        assert await cache.get(workspace_id=tenant_b, space=SPACE, text="shared") is None


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


class TestDatabaseStatementTimeout:
    async def test_a_runaway_statement_is_cancelled_by_the_server(self) -> None:
        """A client-side deadline stops *this* coroutine waiting; the query
        keeps running, keeps its locks, and keeps its connection checked out.
        Only the server cancelling it returns the resource, which is why the
        limit is set on the connection."""
        database = Database(_settings(), statement_timeout_seconds=1)
        try:
            async with database.engine.connect() as conn:
                with pytest.raises(DBAPIError) as cancelled:
                    await conn.execute(text("SELECT pg_sleep(5)"))
        finally:
            await database.dispose()

        # PostgreSQL's `query_canceled`, raised as asyncpg's QueryCanceledError.
        assert "canceling statement" in str(cancelled.value).lower()

    async def test_the_worker_gets_a_longer_ceiling_than_the_api(self) -> None:
        """A re-index batch or a recovery sweep legitimately runs for longer
        than any API request should. Giving the worker the API's limit would
        turn ordinary maintenance into a stream of cancelled statements."""
        settings = _settings()
        api = Database(settings)
        worker = Database(
            settings, statement_timeout_seconds=settings.worker_db_statement_timeout_seconds
        )
        try:
            async with api.engine.connect() as conn:
                api_timeout = await conn.scalar(text("SHOW statement_timeout"))
            async with worker.engine.connect() as conn:
                worker_timeout = await conn.scalar(text("SHOW statement_timeout"))
        finally:
            await api.dispose()
            await worker.dispose()

        assert _to_ms(str(api_timeout)) == settings.db_statement_timeout_seconds * 1000
        assert _to_ms(str(worker_timeout)) > _to_ms(str(api_timeout))

    async def test_every_connection_names_its_process_in_pg_stat_activity(self) -> None:
        """So an operator looking at a long-running query can tell the API's
        connections from the worker's without guessing from the query text."""
        settings = _settings(service_name="orbit-failure-test")
        database = Database(settings)
        try:
            async with database.engine.connect() as conn:
                name = await conn.scalar(text("SHOW application_name"))
        finally:
            await database.dispose()

        assert name == "orbit-failure-test"


def _to_ms(shown: str) -> int:
    """`SHOW statement_timeout` returns a human unit ('15s', '2min', '500ms')."""
    units = {"ms": 1, "s": 1000, "min": 60_000}
    for suffix, factor in sorted(units.items(), key=lambda item: -len(item[0])):
        if shown.endswith(suffix):
            return int(float(shown.removesuffix(suffix)) * factor)
    return int(shown)  # pragma: no cover -- bare number means milliseconds


# ---------------------------------------------------------------------------
# AI provider
# ---------------------------------------------------------------------------


class TestAIProviderUnavailable:
    async def test_search_keeps_working_without_the_embedding_provider(
        self, tenant: Tenant
    ) -> None:
        """The headline degradation from the brief: AI down, search up.

        Asserted against the real database and a real index, because the claim
        is that the *lexical* retriever carries the request -- and that runs
        entirely in PostgreSQL.
        """
        working = tenant.upload_with(tenant.storage, InMemoryLimiter())
        await working.execute(
            tenant.ctx,
            filename="handbook.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(HANDBOOK),
        )

        class DeadEmbedder(FakeEmbeddingProvider):
            async def embed_query(self, text: str) -> list[float]:
                msg = "provider down"
                raise AIProviderUnavailableError(msg)

        uow_factory = make_unit_of_work_factory(tenant.database, cursor_secret="integration")
        search = HybridSearch(
            uow_factory, DeadEmbedder(dimensions=SPACE.dimensions), SearchPolicy(ef_search=40)
        )

        response = await search.execute(tenant.ctx, SearchQuery(text="parental leave"))

        assert response.degraded == SEMANTIC_UNAVAILABLE
        assert response.retrievers == (RetrievalMethod.LEXICAL,)

    async def test_uploads_are_accepted_while_the_provider_is_down(self, tenant: Tenant) -> None:
        """Embedding happens in the worker, on a durable retry schedule. An
        upload that refused because the provider was down would reject work
        the architecture is built to defer.
        """
        upload = tenant.upload_with(tenant.storage, InMemoryLimiter())

        result = await upload.execute(
            tenant.ctx,
            filename="handbook.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(HANDBOOK),
        )

        versions = await tenant.rows(
            "SELECT status FROM document_versions WHERE document_id = :d", d=result.document.id
        )
        assert [version["status"] for version in versions] == ["pending"]
