"""Database engine and session lifecycle.

Sessions are never created ad hoc. They come from here so that pool sizing,
timeouts, recycling, and disposal are configured in exactly one place.

The Celery worker is prefork and synchronous (ADR-0002), so it will eventually
need a second, synchronous engine and driver. That is deliberately *not* built
here: the worker does not touch the database until M4, and adding ``psycopg``
now would ship an unused driver in the production image.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from orbit.core.config import Settings
from orbit.core.logging import get_logger
from orbit.infrastructure.db.instrumentation import instrument_engine, publish_pool_gauges

logger = get_logger(__name__)

# Below typical cloud idle-connection timeouts (AWS RDS proxy and most managed
# Postgres offerings drop idle connections at 30-60 minutes), so the pool never
# hands out a connection the network has already closed.
_POOL_RECYCLE_SECONDS = 1800


def _connect_args(settings: Settings, statement_timeout_seconds: int) -> dict[str, object]:
    """Per-connection server settings, applied by asyncpg at connect time.

    `statement_timeout` is the one that matters operationally. A client-side
    deadline only stops *this* coroutine waiting; the query keeps running,
    keeps its locks, and keeps its connection checked out. PostgreSQL
    cancelling the statement is what actually returns the resource, which is
    why the limit lives on the server rather than in `asyncio.timeout`.

    The two processes need different ceilings, which is why this takes the
    value rather than reading one: an API request that has not answered in
    fifteen seconds has already failed for its caller, while a re-index batch
    or a recovery sweep legitimately runs for minutes. Giving the worker the
    API's limit would turn ordinary maintenance into a stream of cancelled
    statements.
    """
    return {
        "server_settings": {
            # asyncpg requires string values; PostgreSQL parses the unit.
            "statement_timeout": f"{statement_timeout_seconds * 1000}ms",
            # Names this process in `pg_stat_activity`, so an operator looking
            # at a long-running query can tell the API's connections from the
            # worker's without guessing from the query text.
            "application_name": settings.service_name,
        }
    }


class Database:
    """Owns the async engine and session factory for one process."""

    def __init__(self, settings: Settings, *, statement_timeout_seconds: int | None = None) -> None:
        self._engine: AsyncEngine = create_async_engine(
            str(settings.database_url),
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=_POOL_RECYCLE_SECONDS,
            # Costs one round trip per checkout, and removes the entire class of
            # "first query after an idle period fails" incidents.
            pool_pre_ping=True,
            connect_args=_connect_args(
                settings,
                statement_timeout_seconds
                if statement_timeout_seconds is not None
                else settings.db_statement_timeout_seconds,
            ),
        )
        instrument_engine(
            self._engine.sync_engine, slow_query_threshold_ms=settings.db_slow_query_threshold_ms
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            # Entities stay usable after commit; the alternative triggers a
            # refresh query on every attribute read post-commit.
            expire_on_commit=False,
            # Flushes are explicit. Implicit flushing hides write-ordering bugs
            # until they surface as constraint violations in production.
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session and guarantee it is closed.

        Transaction boundaries belong to the use case (ADR-0010), so this does
        not begin or commit -- it manages the connection only.
        """
        async with self._session_factory() as session:
            yield session

    async def ping(self) -> None:
        """Round-trip a trivial query. Propagates the driver error if unreachable."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    def publish_pool_gauges(self) -> None:
        """Refresh the connection-pool gauges. See `publish_pool_gauges`."""
        publish_pool_gauges(self._engine.sync_engine)

    async def dispose(self) -> None:
        """Close every pooled connection. Called on shutdown."""
        await self._engine.dispose()
        logger.info("database.disposed")
