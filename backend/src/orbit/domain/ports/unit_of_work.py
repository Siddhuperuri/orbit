"""The unit-of-work port.

Use cases depend on this protocol, never on the concrete
`infrastructure.db.unit_of_work.UnitOfWork` (enforced by
backend/.importlinter's "use cases depend on ports" contract). The concrete
class satisfies this structurally -- nothing links the two beyond having the
same shape -- which is what lets a test hand a use case an in-memory
implementation with no database at all.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Protocol, Self

from orbit.domain.ports.conversations import ConversationRepository
from orbit.domain.ports.embeddings import EmbeddingIndexRepository
from orbit.domain.ports.processing import ProcessingRepository
from orbit.domain.ports.repositories import (
    AccountTokenRepository,
    AuditRepository,
    DocumentRepository,
    FolderRepository,
    MembershipRepository,
    RefreshTokenRepository,
    TagRepository,
    UserRepository,
    WorkspaceRepository,
)
from orbit.domain.ports.search import SearchRepository


class UnitOfWork(Protocol):
    """One transaction, and the repositories that participate in it.

    The repositories are declared as read-only properties, not plain
    attributes. mypy checks a Protocol's plain attributes for read *and*
    write compatibility, which makes them invariant: a concrete class whose
    `documents` attribute is typed `SqlDocumentRepository` (a subtype of the
    `DocumentRepository` port) would then fail to satisfy this Protocol at
    all, despite being exactly what it is meant to accept. A property is
    read-only, so the check is covariant instead -- which is what "any
    repository implementing this port" is actually supposed to mean.
    """

    @property
    def users(self) -> UserRepository: ...
    @property
    def workspaces(self) -> WorkspaceRepository: ...
    @property
    def memberships(self) -> MembershipRepository: ...
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def folders(self) -> FolderRepository: ...
    @property
    def tags(self) -> TagRepository: ...
    @property
    def refresh_tokens(self) -> RefreshTokenRepository: ...
    @property
    def account_tokens(self) -> AccountTokenRepository: ...
    @property
    def audit(self) -> AuditRepository: ...
    @property
    def processing(self) -> ProcessingRepository: ...
    @property
    def embeddings(self) -> EmbeddingIndexRepository: ...
    @property
    def search(self) -> SearchRepository: ...
    @property
    def conversations(self) -> ConversationRepository: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    async def flush(self) -> None:
        """Send pending statements without ending the transaction."""
        ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


#: What every use case is constructed with: call it, then `async with` the
#: result.
UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]
