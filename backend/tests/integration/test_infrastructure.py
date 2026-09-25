"""Integration tests against the real Compose stack.

These verify the things that cannot be honestly tested against a substitute:
that pgvector is installed and its operators work, that Redis accepts commands,
and that the object storage bucket exists with the configured credentials.

Run `npm run infra:up` first. Marked ``integration`` so the default test command
stays infrastructure-free and fast.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from orbit.core.config import Settings
from orbit.infrastructure.cache.redis import RedisClient
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.health import DatabaseProbe, ObjectStorageProbe, RedisProbe
from orbit.infrastructure.storage.s3 import ObjectStorageClient
from tests.conftest import build_settings

pytestmark = pytest.mark.integration

# Extensions the schema depends on. `vector` is the one that matters: without it
# the entire retrieval design is unimplementable.
_REQUIRED_EXTENSIONS = {"vector", "pg_trgm", "uuid-ossp"}

# pgvector 0.8 introduced iterative index scans, which is the mitigation for
# HNSW under-returning beneath a selective workspace filter (ADR-0005). Below
# this version the retrieval design needs revisiting, so it is asserted here
# rather than discovered during M5.
_MIN_PGVECTOR = (0, 8)


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _settings_from_environment() -> Settings:
    """Build settings pointed at the running Compose stack."""
    required = ("ORBIT_TEST_DATABASE_URL", "ORBIT_REDIS_URL", "ORBIT_S3_BUCKET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"not configured for integration: missing {', '.join(missing)}")

    return build_settings(
        database_url=os.environ["ORBIT_TEST_DATABASE_URL"],
        redis_url=os.environ["ORBIT_REDIS_URL"],
        s3_bucket=os.environ["ORBIT_S3_BUCKET"],
        s3_endpoint_url=os.environ.get("ORBIT_S3_ENDPOINT_URL", "http://localhost:9000"),
        s3_access_key_id=os.environ["ORBIT_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["ORBIT_S3_SECRET_ACCESS_KEY"],
    )


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    db = Database(_settings_from_environment())
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[RedisClient]:
    client = RedisClient(_settings_from_environment())
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def storage() -> ObjectStorageClient:
    return ObjectStorageClient(_settings_from_environment())


class TestPostgres:
    async def test_is_reachable(self, database: Database) -> None:
        await database.ping()

    async def test_required_extensions_are_installed(self, database: Database) -> None:
        async with database.engine.connect() as conn:
            rows = await conn.execute(text("SELECT extname FROM pg_extension"))
            installed = {row[0] for row in rows}
        missing = _REQUIRED_EXTENSIONS - installed
        assert not missing, f"missing extensions: {sorted(missing)}"

    async def test_pgvector_is_recent_enough_for_iterative_scans(self, database: Database) -> None:
        async with database.engine.connect() as conn:
            result = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            raw = result.scalar_one()
        version = tuple(int(part) for part in str(raw).split(".")[:2])
        assert version >= _MIN_PGVECTOR, (
            f"pgvector {raw} predates iterative index scans; filtered-ANN recall "
            f"(ADR-0005) cannot be mitigated below {_MIN_PGVECTOR}"
        )

    async def test_vector_operations_work(self, database: Database) -> None:
        """The extension being present is not the same as it functioning."""
        async with database.engine.connect() as conn:
            result = await conn.execute(
                text("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector AS cosine_distance")
            )
            distance = result.scalar_one()
        # Orthogonal unit vectors have cosine distance 1.
        assert distance == pytest.approx(1.0)

    async def test_probe_reports_healthy(self, database: Database) -> None:
        await DatabaseProbe(database).check()


class TestRedis:
    async def test_is_reachable(self, redis: RedisClient) -> None:
        await redis.ping()

    async def test_accepts_read_and_write(self, redis: RedisClient) -> None:
        key = "orbit:integration:probe"
        await redis.client.set(key, "value", ex=30)
        try:
            assert await redis.client.get(key) == "value"
        finally:
            await redis.client.delete(key)

    async def test_probe_reports_healthy(self, redis: RedisClient) -> None:
        await RedisProbe(redis).check()


class TestObjectStorage:
    def test_bucket_exists_and_credentials_work(self, storage: ObjectStorageClient) -> None:
        storage.head_bucket()

    async def test_probe_reports_healthy(self, storage: ObjectStorageClient) -> None:
        await ObjectStorageProbe(storage).check()

    def test_bucket_denies_anonymous_access(self, storage: ObjectStorageClient) -> None:
        """Provisioning sets the policy to none; a public bucket would expose
        every uploaded document to the internet."""
        endpoint = str(storage.raw.meta.endpoint_url).rstrip("/")
        response = httpx.get(f"{endpoint}/{storage.bucket}/", timeout=5.0)
        assert response.status_code in (401, 403), (
            f"bucket appears anonymously readable (HTTP {response.status_code})"
        )

    async def test_a_document_upload_and_download_round_trip_against_real_minio(
        self, storage: ObjectStorageClient
    ) -> None:
        """`tests/unit/test_s3_adapter.py` verifies this same adapter against
        moto; this is the one test in the suite confirming ORBIT's own
        configuration (endpoint, credentials, path-style addressing) actually
        reaches a *running* S3-compatible service, not just a correct API
        implementation of one."""
        key = f"workspaces/integration-test/documents/{uuid.uuid4()}/{uuid.uuid4()}"
        payload = b"integration test payload"

        try:
            stored = await storage.put_stream(key, _stream(payload), content_type="text/plain")
            assert stored.byte_size == len(payload)
            assert await storage.exists(key) is True

            collected = bytearray()
            async for chunk in storage.open_stream(key):
                collected += chunk
            assert bytes(collected) == payload

            url = await storage.presigned_get_url(
                key, ttl=timedelta(seconds=30), download_filename="integration.txt"
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
            assert response.status_code == 200
            assert response.content == payload
        finally:
            await storage.delete(key)

    async def test_listing_a_workspace_prefix_finds_only_its_own_objects(
        self, storage: ObjectStorageClient
    ) -> None:
        workspace_a = f"workspaces/integration-a-{uuid.uuid4()}/documents/"
        workspace_b = f"workspaces/integration-b-{uuid.uuid4()}/documents/"
        key_a = f"{workspace_a}{uuid.uuid4()}/{uuid.uuid4()}"
        key_b = f"{workspace_b}{uuid.uuid4()}/{uuid.uuid4()}"

        try:
            await storage.put_stream(key_a, _stream(b"a"), content_type="text/plain")
            await storage.put_stream(key_b, _stream(b"b"), content_type="text/plain")

            found = {summary.key async for summary in storage.list_keys(workspace_a)}
            assert found == {key_a}
        finally:
            await storage.delete(key_a)
            await storage.delete(key_b)
