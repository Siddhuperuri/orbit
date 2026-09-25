"""Concrete health probes for each external dependency.

These implement :class:`orbit.domain.ports.health.HealthProbe`. The HTTP layer
never imports this module -- the composition root wires the probes into the
readiness use case, which is what keeps ``api`` free of ``infrastructure``.

Each probe converts its driver's exceptions into a domain error. Driver
exceptions carry hostnames, ports, and occasionally credentials; none of that
may reach a response body or a log line above this layer.
"""

from __future__ import annotations

import anyio
from sqlalchemy.exc import SQLAlchemyError

from orbit.core.logging import get_logger
from orbit.domain.access import SystemContext
from orbit.domain.errors import (
    AIProviderCircuitOpenError,
    ConfigurationError,
    DatabaseUnavailableError,
    StorageUnavailableError,
)
from orbit.infrastructure.ai.resilient_llm import CircuitBreaker
from orbit.infrastructure.cache.redis import RedisClient
from orbit.infrastructure.db.repositories.embeddings import SqlEmbeddingIndexRepository
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.storage.s3 import ObjectStorageClient

logger = get_logger(__name__)


class DatabaseProbe:
    """Verifies PostgreSQL accepts a connection and answers a trivial query."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @property
    def name(self) -> str:
        return "database"

    # Identity, authorization, documents, and job state all live here. There is
    # no degraded mode: an instance without it would serve wrong answers.
    critical = True

    async def check(self) -> None:
        try:
            await self._database.ping()
        except (SQLAlchemyError, OSError) as exc:
            msg = "PostgreSQL is unreachable."
            raise DatabaseUnavailableError(msg) from exc


class RedisProbe:
    """Verifies Redis answers PING."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @property
    def name(self) -> str:
        return "redis"

    # Broker, rate limiter, and cache: each fails open or defers (ADR-0024).
    # Uploads still commit, search still runs.
    critical = False

    async def check(self) -> None:
        # RedisClient.ping already raises DependencyUnavailableError.
        await self._redis.ping()


class ObjectStorageProbe:
    """Verifies the configured bucket exists and the credentials work.

    ``head_bucket`` is used rather than a list or read: it is the cheapest call
    that exercises credentials, network path, and bucket existence together,
    and it requires no objects to be present.
    """

    def __init__(self, storage: ObjectStorageClient) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "object_storage"

    # Uploads and downloads fail with a retryable 503; browsing, search, and
    # chat over already-indexed content keep working.
    critical = False

    async def check(self) -> None:
        # boto3 is synchronous; running it inline would block the event loop and
        # stall every other request while storage is slow -- precisely when
        # readiness matters most.
        try:
            await anyio.to_thread.run_sync(self._storage.head_bucket)
        except StorageUnavailableError:
            raise
        except OSError as exc:
            msg = "Object storage is unreachable."
            raise StorageUnavailableError(msg) from exc


class LanguageModelCircuitProbe:
    """Reports the language model as down while its circuit breaker is open.

    Passive on purpose. Probing a provider means a paid request every few
    seconds from every instance, and a readiness check that calls out to a
    third party fails when *they* do -- the coupling ADR-0007 avoids. The
    breaker already watches real traffic: if it is open, real requests are
    failing, and that is the fact worth surfacing. Not critical: search, upload,
    and browsing do not need the model.
    """

    critical = False

    def __init__(self, circuit: CircuitBreaker) -> None:
        self._circuit = circuit

    @property
    def name(self) -> str:
        return "language_model"

    async def check(self) -> None:
        if self._circuit.is_open:
            msg = "The language model circuit is open."
            raise AIProviderCircuitOpenError(msg)


class EmbeddingSchemaProbe:
    """Verifies the vector column can hold the configured embedding space.

    ADR-0007 requires that configuration disagreeing with the schema is a
    refusal, not a slow corruption. Startup deliberately makes no database
    call (a dependency blip must not stop a process booting), so the refusal
    lives here: an instance whose `ORBIT_EMBEDDING_DIMENSIONS` does not match
    `chunks.embedding` never reports ready and never receives traffic.
    """

    def __init__(self, database: Database, *, expected_dimensions: int) -> None:
        self._database = database
        self._expected = expected_dimensions

    @property
    def name(self) -> str:
        return "embedding_schema"

    # A mismatch corrupts rankings silently; it must stop traffic (ADR-0007).
    critical = True

    async def check(self) -> None:
        try:
            async with self._database.session() as session:
                column = await SqlEmbeddingIndexRepository(session).column_dimensions(
                    SystemContext(reason="readiness")
                )
        except (SQLAlchemyError, OSError) as exc:
            msg = "PostgreSQL is unreachable."
            raise DatabaseUnavailableError(msg) from exc
        if column != self._expected:
            logger.error(
                "readiness.embedding_schema_mismatch",
                configured_dimensions=self._expected,
                column_dimensions=column,
            )
            msg = "ORBIT_EMBEDDING_DIMENSIONS does not match the vector column."
            raise ConfigurationError(msg, configured=self._expected, column=column)
