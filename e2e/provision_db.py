"""Create and reset the end-to-end database.

Run through the backend's environment (`uv run`), because it borrows that
project's asyncpg rather than adding a driver to the Node package.

Two steps, in this order and for the same reason the integration suite does
them: the schema is **dropped and rebuilt by the migrations** on every run, so
"the migrations apply to an empty database" is something each E2E run proves
rather than assumes, and no run inherits rows from the last one.

The database name is passed in, and this script refuses to touch anything that
is not clearly an E2E database -- dropping the schema is destructive, and a
typo in an environment variable must not be able to aim it at `orbit`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlsplit

import asyncpg
import redis.asyncio as redis


def _dsn(url: str) -> str:
    """SQLAlchemy's `postgresql+asyncpg://` prefix is not a libpq scheme."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> int:
    admin_url = os.environ["E2E_ADMIN_DATABASE_URL"]
    target_url = os.environ["E2E_DATABASE_URL"]

    name = urlsplit(_dsn(target_url)).path.lstrip("/")
    if "e2e" not in name:
        sys.stderr.write(
            f"refusing to provision {name!r}: the end-to-end database name must contain 'e2e', "
            "because this script drops its schema\n"
        )
        return 2

    admin = await asyncpg.connect(_dsn(admin_url))
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            # Identifier, not a value, so it cannot be a bound parameter; the
            # name is constrained above and comes from configuration, not input.
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()

    target = await asyncpg.connect(_dsn(target_url))
    try:
        # CASCADE also removes the enum types that a plain DROP TABLE leaves
        # behind, which would make the next migration fail with "type already
        # exists".
        await target.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await target.execute("CREATE SCHEMA public")
        for extension in ("vector", "pg_trgm", "uuid-ossp"):
            await target.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
    finally:
        await target.close()

    await _flush_redis()

    sys.stdout.write(f"e2e database {name} reset\n")
    return 0


async def _flush_redis() -> None:
    """Empty the E2E Redis databases.

    Rate-limit counters live in Redis with an hour-long window. Left behind,
    the previous run's counters are still ticking when the next one starts, so
    a suite that passes once fails ten minutes later for reasons that have
    nothing to do with the code. Flushing makes each run start from zero.

    Only the databases this environment owns (12-14, set in `env.ts`) are
    touched; the unit suite's 9-11 and production's 0-2 are not.
    """
    for url in os.environ["E2E_REDIS_URLS"].split(","):
        client = redis.from_url(url.strip())
        try:
            await client.flushdb()
        finally:
            await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
