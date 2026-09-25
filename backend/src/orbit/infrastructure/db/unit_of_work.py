"""Unit of work.

The transaction boundary belongs to the use case, not to the repository
(ADR-0010). A repository that committed on its own would make multi-repository
invariants impossible to express -- and "create a workspace *and* make its
creator the owner" is exactly such an invariant: a workspace with no owner must
never be a state that reaches disk.

Usage:

    async with uow_factory() as uow:
        workspace = await uow.workspaces.create(...)
        await uow.memberships.add(...)
        await uow.commit()

Exiting without `commit()` rolls back. That is deliberate: forgetting to commit
loses work loudly in a test, whereas an implicit commit would silently persist a
half-finished operation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Self

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.logging import get_logger
from orbit.domain.errors import DatabaseUnavailableError
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.infrastructure.db.errors import (
    flush_translating_conflicts,
    translate_integrity_error,
)
from orbit.infrastructure.db.repositories.account_tokens import SqlAccountTokenRepository
from orbit.infrastructure.db.repositories.audit import SqlAuditRepository
from orbit.infrastructure.db.repositories.conversations import SqlConversationRepository
from orbit.infrastructure.db.repositories.documents import SqlDocumentRepository
from orbit.infrastructure.db.repositories.embeddings import SqlEmbeddingIndexRepository
from orbit.infrastructure.db.repositories.folders import SqlFolderRepository
from orbit.infrastructure.db.repositories.memberships import SqlMembershipRepository
from orbit.infrastructure.db.repositories.processing import SqlProcessingRepository
from orbit.infrastructure.db.repositories.refresh_tokens import SqlRefreshTokenRepository
from orbit.infrastructure.db.repositories.search import SqlSearchRepository
from orbit.infrastructure.db.repositories.tags import SqlTagRepository
from orbit.infrastructure.db.repositories.users import SqlUserRepository
from orbit.infrastructure.db.repositories.workspaces import SqlWorkspaceRepository
from orbit.infrastructure.db.session import Database

logger = get_logger(__name__)


class UnitOfWork:
    """One transaction, and the repositories that participate in it."""

    def __init__(self, session: AsyncSession, *, cursor_secret: str) -> None:
        self._session = session
        self._committed = False

        self.users = SqlUserRepository(session)
        self.workspaces = SqlWorkspaceRepository(session)
        self.memberships = SqlMembershipRepository(session)
        self.documents = SqlDocumentRepository(session, cursor_secret=cursor_secret)
        self.folders = SqlFolderRepository(session)
        self.tags = SqlTagRepository(session)
        self.refresh_tokens = SqlRefreshTokenRepository(session)
        self.account_tokens = SqlAccountTokenRepository(session)
        self.audit = SqlAuditRepository(session)
        self.processing = SqlProcessingRepository(session)
        self.embeddings = SqlEmbeddingIndexRepository(session)
        self.search = SqlSearchRepository(session)
        self.conversations = SqlConversationRepository(session, cursor_secret=cursor_secret)

    @property
    def session(self) -> AsyncSession:
        """Escape hatch for a query no repository covers yet.

        Used by tests and by administrative tooling. Application code reaching
        for this is a signal that a repository method is missing.
        """
        return self._session

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
        except DBAPIError as exc:
            await self._session.rollback()
            msg = "The database rejected the transaction."
            raise DatabaseUnavailableError(msg) from exc
        self._committed = True

    async def rollback(self) -> None:
        await self._session.rollback()

    async def flush(self) -> None:
        """Send pending statements without ending the transaction.

        Needed when a later statement depends on a constraint being checked, or
        on a server-generated default. Constraint violations surface here rather
        than at commit, which makes them attributable to the statement that
        caused them.
        """
        await flush_translating_conflicts(self._session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            # Covers both the exception path and a use case that simply forgot.
            # Losing uncommitted work is the safe failure; persisting a partial
            # operation is not.
            await self._session.rollback()


def make_unit_of_work_factory(database: Database, *, cursor_secret: str) -> UnitOfWorkFactory:
    """Build the factory the composition root injects into use cases.

    Declared to return the port's `UnitOfWorkFactory`, not a factory typed by
    this module's concrete `UnitOfWork`. The two are structurally identical
    -- the concrete class satisfies the port's Protocol exactly -- which is
    what makes this assignment-compatible with no cast required.
    """

    @asynccontextmanager
    async def factory() -> AsyncIterator[UnitOfWork]:
        async with database.session() as session:
            uow = UnitOfWork(session, cursor_secret=cursor_secret)
            async with uow:
                yield uow

    return factory
