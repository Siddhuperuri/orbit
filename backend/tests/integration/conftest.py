"""Integration fixtures: a real PostgreSQL database, migrated and isolated.

Two decisions shape this file.

**The schema is created by running the migrations**, not by
`Base.metadata.create_all()`. Creating from metadata would test a schema that no
deployment ever produces, and would hide exactly the defects that matter -- a
migration that does not apply, or one that drifts from the models.

**Each test runs inside a transaction that is rolled back.** The alternative,
truncating tables between tests, is an order of magnitude slower and leaves the
sequence and enum state behind. Rolling back gives perfect isolation and lets
the whole suite share one migrated database.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine

from orbit.core.config import Settings
from orbit.domain.access import AccessContext, Role
from orbit.domain.models.entities import Membership, User, VersionContent, Workspace
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.conftest import build_settings

CURSOR_SECRET = "integration-test-cursor-secret"


def _database_url() -> str:
    url = os.environ.get("ORBIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "ORBIT_TEST_DATABASE_URL is not set. Start infrastructure with "
            "`npm run infra:up`, then re-run."
        )
    return url


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    return build_settings(database_url=_database_url())


@pytest.fixture(scope="session")
def alembic_config(integration_settings: Settings) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(integration_settings.database_url))
    return config


@pytest.fixture(scope="session")
async def migrated_engine(integration_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """A database whose schema was built by the migrations, from empty.

    The drop-then-migrate is what makes "run migrations from a clean database" a
    thing the suite actually verifies on every run, rather than a claim.
    """
    engine = create_async_engine(str(integration_settings.database_url), poolclass=None)

    async with engine.begin() as connection:
        # CASCADE also removes the enum types, which `DROP TABLE` leaves behind
        # and which would make the next migration fail with "type already
        # exists" -- the same failure a rollback-then-roll-forward would hit.
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
        # Extensions live in the schema that was just dropped, so they are
        # recreated here. In production this is provisioning's job; the test
        # database has no such separation of privilege.
        for extension in ("vector", "pg_trgm", "uuid-ossp"):
            await connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))

    await _run_migrations(engine, "head")

    yield engine
    await engine.dispose()


async def _run_migrations(engine: AsyncEngine, revision: str) -> None:
    """Run Alembic against the test database.

    Alembic's command API is synchronous, so it runs on the connection's
    underlying sync driver via `run_sync`.
    """

    def upgrade(connection: object) -> None:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    async with engine.begin() as connection:
        await connection.run_sync(upgrade)


@pytest.fixture
async def connection(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection with an open transaction that is always rolled back.

    Every statement a test issues happens inside this transaction, so the
    database is byte-identical before and after -- no cleanup code, no ordering
    dependencies between tests.
    """
    async with migrated_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session joined to the test's outer transaction.

    `join_transaction_mode="create_savepoint"` means a `session.commit()` inside
    a test releases a savepoint rather than committing for real, so code under
    test can commit normally while the outer rollback still undoes everything.
    """
    async with AsyncSession(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    ) as sess:
        yield sess


@pytest.fixture
async def uow(session: AsyncSession) -> UnitOfWork:
    return UnitOfWork(session, cursor_secret=CURSOR_SECRET)


# ---------------------------------------------------------------------------
# Factories
#
# Deliberately explicit rather than a factory library: the fixtures below are
# the only place a test's setup can hide, so they stay small and readable.
# ---------------------------------------------------------------------------


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.test"


def unique_slug(prefix: str = "ws") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256_of(marker: str) -> str:
    """A deterministic, unique 64-character hex digest for a test."""
    return hashlib.sha256(marker.encode()).hexdigest()


def version_content(marker: str = "doc", *, byte_size: int = 1024) -> VersionContent:
    digest = sha256_of(marker)
    return VersionContent(
        storage_key=f"workspaces/test/documents/test/{digest}",
        content_sha256=digest,
        byte_size=byte_size,
        content_type="application/pdf",
        original_filename=f"{marker}.pdf",
    )


@pytest.fixture
async def user(uow: UnitOfWork) -> User:
    return await uow.users.create(
        email=unique_email(), password_hash="$argon2id$fake$hash", full_name="Test User"
    )


@pytest.fixture
async def other_user(uow: UnitOfWork) -> User:
    return await uow.users.create(
        email=unique_email("other"), password_hash="$argon2id$fake$hash", full_name="Other User"
    )


@pytest.fixture
async def workspace(uow: UnitOfWork, user: User) -> Workspace:
    """A workspace with its creator seeded as owner, as production does."""
    created = await uow.workspaces.create(
        name="Test Workspace", slug=unique_slug(), created_by_user_id=user.id
    )
    await uow.memberships.add_owner(created.id, user.id)
    return created


@pytest.fixture
def ctx(workspace: Workspace, user: User) -> AccessContext:
    return AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)


@pytest.fixture
async def other_workspace(uow: UnitOfWork, other_user: User) -> Workspace:
    """A second tenant, for cross-tenant isolation tests."""
    created = await uow.workspaces.create(
        name="Other Workspace", slug=unique_slug("other"), created_by_user_id=other_user.id
    )
    await uow.memberships.add_owner(created.id, other_user.id)
    return created


@pytest.fixture
def other_ctx(other_workspace: Workspace, other_user: User) -> AccessContext:
    return AccessContext(user_id=other_user.id, workspace_id=other_workspace.id, role=Role.OWNER)


@pytest.fixture
async def membership(uow: UnitOfWork, ctx: AccessContext, other_user: User) -> Membership:
    return await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
