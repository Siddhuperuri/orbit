"""Repository ports.

Every workspace-scoped method takes an `AccessContext` as a **required**
parameter. That is the mechanism, not decoration: there is no overload that
omits it, so forgetting a tenant filter is a type error under `mypy --strict`
rather than a silent cross-tenant read (ADR-0004).

Account-level repositories (`UserRepository`) take no context, because an
account exists before any workspace membership does. They are correspondingly
few, and every method on them is a deliberate exception to tenant scoping.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import DocumentEdit, DocumentListQuery, PassageSlice
from orbit.domain.models.entities import (
    AccountToken,
    AccountTokenPurpose,
    Document,
    DocumentVersion,
    Folder,
    FolderListing,
    Membership,
    NewDocumentIds,
    NewRefreshToken,
    ProcessingOutcome,
    RefreshTokenSession,
    Tag,
    TagListing,
    User,
    VersionContent,
    VersionPage,
    Workspace,
)
from orbit.domain.models.pagination import Page
from orbit.domain.ports.audit import AuditEvent


class UserRepository(Protocol):
    """Accounts. Not workspace-scoped: an account precedes any membership."""

    async def get(self, user_id: uuid.UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup. Returns soft-deleted users as `None`."""
        ...

    async def get_password_hash(self, user_id: uuid.UUID) -> str | None:
        """Deliberately separate from `get`.

        The hash is needed at exactly one call site -- login -- so it is not
        carried on the `User` entity, where it would end up in logs, tracebacks,
        and anything that serialises an entity.
        """
        ...

    async def create(self, *, email: str, password_hash: str, full_name: str) -> User: ...

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Replaces the hash and bumps the token epoch, invalidating live sessions.

        For a *deliberate* password change only. To re-encode an unchanged
        password under stronger parameters, use `upgrade_password_hash`.
        """
        ...

    async def upgrade_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Re-encodes the same password without invalidating live sessions."""
        ...

    async def mark_email_verified(self, user_id: uuid.UUID) -> None:
        """Idempotent: re-verifying an already-verified address is a no-op.

        Stamping the time again would rewrite history for no benefit, and the
        column doubles as the "has this ever been proven?" flag.
        """
        ...

    async def record_login(self, user_id: uuid.UUID) -> None: ...

    async def soft_delete(self, user_id: uuid.UUID) -> None: ...


class WorkspaceRepository(Protocol):
    async def get(self, ctx: AccessContext) -> Workspace | None:
        """The workspace named by the context. There is no `get(id)`."""
        ...

    async def create(self, *, name: str, slug: str, created_by_user_id: uuid.UUID) -> Workspace:
        """Creating a workspace precedes membership in it, so it takes no context.

        The caller becomes its owner in the same transaction; a workspace with
        no owner is not a state the application ever commits.
        """
        ...

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[tuple[Workspace, Role]]: ...

    async def rename(self, ctx: AccessContext, name: str, *, expected_version: int) -> Workspace:
        """Optimistic update. Raises `ConflictError` if the row moved underneath."""
        ...

    async def soft_delete(self, ctx: AccessContext) -> None: ...


class MembershipRepository(Protocol):
    async def get(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        """Resolves the caller's role. Runs before an `AccessContext` exists, so
        it is the one membership method that takes raw identifiers."""
        ...

    async def list_members(self, ctx: AccessContext) -> Sequence[Membership]: ...

    async def add(self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role) -> Membership: ...

    async def add_owner(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
        """Seed the first owner, at workspace creation.

        The one membership write with no `AccessContext`: at this moment the
        caller has no role in a workspace that did not exist a statement ago.
        Callable only from workspace creation, in the same transaction, so a
        workspace with no owner is never committed.
        """
        ...

    async def change_role(
        self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role
    ) -> Membership: ...

    async def remove(self, ctx: AccessContext, *, user_id: uuid.UUID) -> None: ...

    async def count_owners(self, workspace_id: uuid.UUID) -> int:
        """Supports the last-owner rule, which no table constraint can express."""
        ...


class DocumentRepository(Protocol):
    async def get(self, ctx: AccessContext, document_id: uuid.UUID) -> Document | None:
        """Returns `None` for another tenant's document, never a permission error.

        A 403 would confirm the document exists and leak it across tenants.
        """
        ...

    async def find_by_content_hash(
        self, ctx: AccessContext, content_sha256: str
    ) -> Document | None:
        """Deduplication lookup: an identical re-upload returns the existing
        document rather than reprocessing the same bytes (ADR-0011)."""
        ...

    async def create(
        self,
        ctx: AccessContext,
        *,
        title: str,
        folder_id: uuid.UUID | None,
        content: VersionContent,
        ids: NewDocumentIds | None = None,
    ) -> Document:
        """Creates the document and its first version in one statement pair.

        `ids` is `None` for every caller except the upload pipeline, which
        pre-generates both ids it carries: ADR-0011's storage-key format is
        built from them and the object must exist in storage *before* this row
        is written, so they have to be chosen first. Every other caller lets
        the repository generate its own, unchanged from before upload existed.

        Both rows are written inside the caller's transaction; a document with no
        version is never committed.
        """
        ...

    async def add_version(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        content: VersionContent,
        version_id: uuid.UUID | None = None,
    ) -> DocumentVersion:
        """Adds a revision and makes it current, demoting the previous one.

        See `create`'s docstring for why `version_id` can be pre-generated.
        """
        ...

    async def exists_by_storage_key(self, storage_key: str) -> bool:
        """Whether any version -- current or superseded, any workspace --
        still references this key.

        Deliberately not workspace-scoped: it exists for the orphan sweep,
        which walks storage key-by-key and asks this one question per key. The
        key itself already names its workspace (`core/storage_keys.py`), so
        scoping the lookup again would just repeat what the key already says.
        """
        ...

    async def list_versions(
        self, ctx: AccessContext, document_id: uuid.UUID
    ) -> Sequence[DocumentVersion]: ...

    async def page_versions(
        self, ctx: AccessContext, document_id: uuid.UUID, *, before: int | None, limit: int
    ) -> VersionPage:
        """A document's revisions, newest first. Returns an empty page for a
        document the caller cannot see, exactly as `get` returns `None`."""
        ...

    async def get_version(
        self, ctx: AccessContext, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> DocumentVersion | None:
        """One revision, current or superseded. `None` unless it belongs to a
        live document in the caller's workspace."""
        ...

    async def list_passages(
        self, ctx: AccessContext, version_id: uuid.UUID, *, after: int | None, limit: int
    ) -> PassageSlice:
        """The stored passages of one version, in reading order.

        Only the *current* version has any (superseded versions lose their
        chunks -- `models/content.py`), so this is empty for every other one.
        """
        ...

    async def set_version_status(
        self, ctx: AccessContext, version_id: uuid.UUID, *, outcome: ProcessingOutcome
    ) -> DocumentVersion: ...

    async def update(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        edit: DocumentEdit,
        expected_version: int,
    ) -> Document:
        """Rename and/or refile, under optimistic concurrency.

        Returns the whole document -- current version and tags included -- so a
        client can replace its copy with the response rather than patching a
        partial one.

        Raises `ConflictError` if the row moved since `expected_version`,
        `NotFoundError` if the document -- or the target folder -- is gone.
        """
        ...

    async def set_archived(
        self, ctx: AccessContext, document_id: uuid.UUID, *, archived: bool
    ) -> Document:
        """Archive or restore. Idempotent: asking for the state a document is
        already in succeeds and changes nothing, because "make it archived"
        has one right answer however many people asked."""
        ...

    async def soft_delete(self, ctx: AccessContext, document_id: uuid.UUID) -> None: ...

    async def list_page(
        self,
        ctx: AccessContext,
        *,
        limit: int,
        cursor: str | None = None,
        query: DocumentListQuery | None = None,
    ) -> Page:
        """Keyset-paginated list. There is no unbounded variant."""
        ...


class FolderRepository(Protocol):
    async def list_all(self, ctx: AccessContext) -> Sequence[FolderListing]:
        """Every live folder, parents before children, with what each holds.

        Returned whole because a tree cannot be paged; the count is bounded by
        `MAX_FOLDERS_PER_WORKSPACE`, enforced on creation.
        """
        ...

    async def get(self, ctx: AccessContext, folder_id: uuid.UUID) -> Folder | None: ...

    async def count(self, ctx: AccessContext) -> int: ...

    async def create(self, ctx: AccessContext, *, name: str, parent_id: uuid.UUID | None) -> Folder:
        """`NotFoundError` if the parent is gone, `ConflictError` if a sibling
        already has the name (compared case-insensitively)."""
        ...

    async def rename(
        self, ctx: AccessContext, folder_id: uuid.UUID, *, name: str, expected_version: int
    ) -> Folder: ...

    async def delete(self, ctx: AccessContext, folder_id: uuid.UUID) -> None:
        """Raises `FolderNotEmptyError` unless the folder holds nothing.

        The folder row is locked for the check, and filing a document takes a
        shared lock on it, so a delete cannot slip between "it was empty" and a
        concurrent upload landing in it.
        """
        ...


class TagRepository(Protocol):
    async def list_all(self, ctx: AccessContext) -> Sequence[TagListing]: ...

    async def get(self, ctx: AccessContext, tag_id: uuid.UUID) -> Tag | None: ...

    async def count(self, ctx: AccessContext) -> int: ...

    async def create(self, ctx: AccessContext, *, name: str, color: str) -> Tag: ...

    async def update(
        self,
        ctx: AccessContext,
        tag_id: uuid.UUID,
        *,
        name: str | None,
        color: str | None,
        expected_version: int,
    ) -> Tag: ...

    async def delete(self, ctx: AccessContext, tag_id: uuid.UUID) -> None:
        """Removes the tag from every document that carries it."""
        ...

    async def attach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        """Idempotent. `True` if this call added the tag, `False` if the
        document already carried it. `NotFoundError` if either is gone."""
        ...

    async def detach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        """Idempotent: removing a tag that is not there succeeds."""
        ...

    async def count_for_document(self, ctx: AccessContext, document_id: uuid.UUID) -> int: ...


class RefreshTokenRepository(Protocol):
    """Session tokens. Not workspace-scoped -- a session belongs to a user."""

    async def create(self, issuance: NewRefreshToken) -> RefreshTokenSession: ...

    async def get_by_hash(self, token_hash: str) -> RefreshTokenSession | None: ...

    async def consume(self, token_id: uuid.UUID) -> None:
        """Mark a token spent. A second consume of the same token is what
        reuse detection watches for -- it is the caller's job to notice that
        `get_by_hash` already returned a consumed row before calling this."""
        ...

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        """Invalidate every token descended from one login, on reuse
        detection or explicit logout-everywhere."""
        ...

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Invalidate every session this user has, across all families.

        Refresh tokens carry no token epoch, so bumping `users.token_epoch`
        alone would leave a stolen one able to mint fresh access tokens after
        a password reset.
        """
        ...


class AccountTokenRepository(Protocol):
    """One-time tokens mailed to a user: password reset, email verification."""

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        purpose: AccountTokenPurpose,
        token_hash: str,
        expires_at: datetime,
        client_ip: str | None = None,
    ) -> AccountToken: ...

    async def get_by_hash(
        self, token_hash: str, *, purpose: AccountTokenPurpose
    ) -> AccountToken | None:
        """Looks up by hash **and** purpose, so a token issued for one flow
        does not exist as far as another flow's query is concerned."""
        ...

    async def consume(self, token_id: uuid.UUID) -> bool:
        """Compare-and-set. Returns `False` if it was already spent, which is
        what makes concurrent redemption of one token safe."""
        ...

    async def invalidate_outstanding(
        self, *, user_id: uuid.UUID, purpose: AccountTokenPurpose
    ) -> None:
        """Spends every unredeemed token of this purpose, so issuing a new one
        supersedes any still sitting in an inbox."""
        ...


class AuditRepository(Protocol):
    """Append-only security event log. No update, no delete."""

    async def append(self, event: AuditEvent) -> uuid.UUID: ...
