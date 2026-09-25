"""Redis connection management.

Redis is the Celery broker and the application cache. It is deliberately **not**
authoritative for anything: losing it costs in-flight jobs, never committed data
(see docs/architecture/system.md).

``redis.asyncio`` ships with the ``redis`` package, so no separate async client
dependency is required.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from orbit.core.config import Settings
from orbit.core.logging import get_logger
from orbit.domain.errors import DependencyUnavailableError

logger = get_logger(__name__)


class RedisClient:
    """Owns the connection pool for one process."""

    def __init__(self, settings: Settings) -> None:
        self._client: Redis = Redis.from_url(
            str(settings.redis_url),
            decode_responses=True,
            # Bounded so a hung Redis cannot occupy a request worker forever.
            # Configured rather than hard-coded: the right value depends on
            # where Redis sits relative to the process, and the failure these
            # bound -- a wedged connection holding a request worker -- is
            # exactly the one an operator needs to be able to tune under load.
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_command_timeout_seconds,
            # Detects dropped connections that a managed Redis or a NAT gateway
            # closed while idle, instead of failing the next real command.
            health_check_interval=30,
        )

    @property
    def client(self) -> Redis:
        return self._client

    async def ping(self) -> None:
        """Round-trip a PING, converting driver errors to a domain error."""
        try:
            await self._client.ping()
        except (RedisError, OSError) as exc:
            # Redis errors carry host and port; they stop here.
            msg = "Redis is unreachable."
            raise DependencyUnavailableError(msg) from exc

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("redis.closed")
