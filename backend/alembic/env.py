"""Alembic environment.

The database URL comes from ORBIT's own settings rather than from
``alembic.ini``. Keeping one source of configuration means a migration can never
be applied to a different database than the application talks to -- and it keeps
credentials out of a committed ini file.

Migrations are run as a pre-deploy job, never on application start: concurrent
API instances starting together would race to apply the same migration.
See docs/architecture/system.md.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from orbit.core.config import get_settings

# Importing the models package registers every ORM model against this Base,
# which is what makes autogenerate able to see the target schema. (It must be
# the models' Base: an unrelated DeclarativeBase has empty metadata, and
# autogenerate would then propose dropping every table.)
from orbit.infrastructure.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_settings = get_settings()
# `%` is the interpolation character in ini files; a password containing one
# would otherwise corrupt the URL.
config.set_main_option("sqlalchemy.url", str(_settings.database_url).replace("%", "%%"))


def _include_object(
    obj: Any,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    """Keep autogenerate focused on tables ORBIT owns.

    Without this, autogenerate proposes dropping anything it finds in the
    database that is not in the metadata -- including extension-owned tables.
    """
    del obj
    if type_ == "table" and name is not None:
        return not name.startswith(("pg_", "sql_"))
    return True


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        # Detect column type changes; off by default, and its absence is a
        # common source of migrations that silently miss a widening.
        compare_type=True,
        compare_server_default=True,
        # Render the schema explicitly so migrations are unambiguous when the
        # connection's search_path differs between environments.
        include_schemas=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    Used to review the exact statements a deploy will run before it runs them.
    """
    context.configure(
        url=str(_settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: a migration process is short-lived and single-use, so pooling
        # would only hold connections open after the work is done.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


def _run_online() -> None:
    # A caller may inject its own connection -- the integration suite does, so
    # that migrations run against the test database rather than whichever URL
    # the environment happens to name. That caller is already inside an event
    # loop (it reached here through `AsyncConnection.run_sync`), so the injected
    # path must stay synchronous: `asyncio.run` from within a running loop
    # raises immediately.
    injected = config.attributes.get("connection")
    if injected is not None:
        _run_migrations(injected)
        return
    asyncio.run(run_migrations_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    _run_online()
