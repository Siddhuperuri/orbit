"""An in-memory `UnitOfWork` for testing use cases with no database.

This exists to test two things the SQL-backed integration suite cannot cover
cheaply: that use cases depend only on the *port* (swap the implementation and
nothing else changes -- the dependency-injection contract), and that
authorization boundaries hold at the use-case layer in isolation from any
particular storage engine.

It deliberately does not reproduce every constraint the real schema enforces
(uniqueness, foreign keys) -- that is what the integration suite is for. It
reproduces enough behaviour (transactional snapshot/rollback, "not found"
semantics, the last-owner rule) that use-case tests read the same way against
either implementation.
"""

from __future__ import annotations

import copy
import dataclasses
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

from tests.unit.fakes.fake_conversations import FakeConversationRepository
from tests.unit.fakes.fake_processing import (
    FakeEmbeddingIndexRepository,
    FakeProcessingRepository,
    FakeSearchRepository,
    StoredChunk,
)

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext, Role
from orbit.domain.conversations import ChatMessage, Conversation
from orbit.domain.documents import (
    ArchiveFilter,
    DocumentEdit,
    DocumentListQuery,
    DocumentSort,
    Passage,
    PassageSlice,
)
from orbit.domain.errors import ConflictError, FolderNotEmptyError, NotFoundError, ValidationError
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
    ProcessingStatus,
    RefreshTokenSession,
    Tag,
    TagListing,
    TagRef,
    User,
    VersionContent,
    VersionPage,
    Workspace,
)
from orbit.domain.models.pagination import Page, SortCursor
from orbit.domain.organization import MAX_FOLDER_DEPTH
from orbit.domain.ports.audit import AuditEvent
from orbit.domain.ports.unit_of_work import UnitOfWork as UnitOfWorkPort
from orbit.domain.processing.jobs import ProcessingJob


@dataclass
class FakeControls:
    #: Jobs "another transaction" holds a row lock on.
    locked_job_ids: set[uuid.UUID] = field(default_factory=set)
    #: When set, the next `replace_chunks` writes partially and raises this.
    fail_next_index: BaseException | None = None
    #: Timestamp source for job rows, so they agree with a test's FixedClock.
    clock_now: Callable[[], datetime] | None = None
    #: The width the fake "vector column" accepts; `None` for untyped.
    column_dimensions: int | None = 32
    #: Workspaces for which embedding reuse was looked up, in call order.
    reuse_lookups: list[uuid.UUID] = field(default_factory=list)
    #: When set, every conversation write raises this (a database outage).
    fail_conversation_writes: BaseException | None = None


@dataclass
class _State:
    """Everything the fake holds, snapshotted whole for rollback.

    A real transaction isolates at the row level; this isolates at the
    process level, which is coarser but sufficient for exercising use-case
    logic -- no test here runs two fakes concurrently against one `_State`.
    """

    users: dict[uuid.UUID, User] = field(default_factory=dict)
    password_hashes: dict[uuid.UUID, str] = field(default_factory=dict)
    workspaces: dict[uuid.UUID, Workspace] = field(default_factory=dict)
    memberships: dict[tuple[uuid.UUID, uuid.UUID], Membership] = field(default_factory=dict)
    documents: dict[uuid.UUID, Document] = field(default_factory=dict)
    versions: dict[uuid.UUID, DocumentVersion] = field(default_factory=dict)
    folders: dict[uuid.UUID, Folder] = field(default_factory=dict)
    deleted_folders: set[uuid.UUID] = field(default_factory=set)
    tags: dict[uuid.UUID, Tag] = field(default_factory=dict)
    #: (document_id, tag_id) pairs.
    document_tags: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)
    #: The last timestamp handed to a document, so the next is strictly later.
    last_tick: datetime | None = None
    refresh_tokens: dict[uuid.UUID, RefreshTokenSession] = field(default_factory=dict)
    account_tokens: dict[uuid.UUID, AccountToken] = field(default_factory=dict)
    audit_events: list[AuditEvent] = field(default_factory=list)
    jobs: dict[uuid.UUID, ProcessingJob] = field(default_factory=dict)
    chunks: dict[uuid.UUID, list[StoredChunk]] = field(default_factory=dict)
    conversations: dict[uuid.UUID, Conversation] = field(default_factory=dict)
    deleted_conversations: set[uuid.UUID] = field(default_factory=set)
    messages: dict[uuid.UUID, ChatMessage] = field(default_factory=dict)
    #: Knobs a test turns. Shared by reference, never snapshotted: a rollback
    #: must not un-fire a one-shot failure, or it would fire forever.
    controls: FakeControls = field(default_factory=FakeControls)

    def __deepcopy__(self, memo: dict[int, object]) -> _State:
        clone = _State.__new__(_State)
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            setattr(clone, f.name, value if f.name == "controls" else copy.deepcopy(value, memo))
        return clone


def _tick(state: _State) -> datetime:
    """A timestamp strictly later than every one this state handed out before.

    The wall clock ties when two writes land within its resolution, and a tie
    falls through to the row id -- random within a millisecond. That would make
    "newest first" tests pass or fail by luck. PostgreSQL gives every statement in
    a transaction the same `now()`, so the real tiebreak is the id there too; what
    a *use-case* test needs is a clock that never repeats.
    """
    now = datetime.now(UTC)
    if state.last_tick is not None and now <= state.last_tick:
        now = state.last_tick + timedelta(microseconds=1)
    state.last_tick = now
    return now


class FakeUserRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def get(self, user_id: uuid.UUID) -> User | None:
        user = self._state.users.get(user_id)
        return user if user and not user.is_deleted else None

    async def get_by_email(self, email: str) -> User | None:
        for user in self._state.users.values():
            if user.email.lower() == email.lower() and not user.is_deleted:
                return user
        return None

    async def get_password_hash(self, user_id: uuid.UUID) -> str | None:
        return self._state.password_hashes.get(user_id)

    async def create(self, *, email: str, password_hash: str, full_name: str) -> User:
        if await self.get_by_email(email) is not None:
            msg = "An account with that email address already exists."
            raise ConflictError(msg)
        user = User(
            id=new_uuid7(),
            email=email,
            full_name=full_name,
            is_active=True,
            token_epoch=0,
            created_at=datetime.now(UTC),
        )
        self._state.users[user.id] = user
        self._state.password_hashes[user.id] = password_hash
        return user

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        self._state.password_hashes[user_id] = password_hash
        user = self._state.users[user_id]
        self._state.users[user_id] = dataclasses.replace(user, token_epoch=user.token_epoch + 1)

    async def upgrade_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Re-encodes the same password. Deliberately leaves `token_epoch`
        alone -- see the SQL adapter for why conflating this with
        `set_password` produces a dead-on-arrival session."""
        self._state.password_hashes[user_id] = password_hash

    async def mark_email_verified(self, user_id: uuid.UUID) -> None:
        user = self._state.users[user_id]
        if user.email_verified_at is not None:
            return
        self._state.users[user_id] = dataclasses.replace(user, email_verified_at=datetime.now(UTC))

    async def record_login(self, user_id: uuid.UUID) -> None:
        user = self._state.users[user_id]
        self._state.users[user_id] = dataclasses.replace(user, last_login_at=datetime.now(UTC))

    async def soft_delete(self, user_id: uuid.UUID) -> None:
        user = self._state.users[user_id]
        self._state.users[user_id] = dataclasses.replace(
            user, deleted_at=datetime.now(UTC), is_active=False, token_epoch=user.token_epoch + 1
        )


class FakeWorkspaceRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def get(self, ctx: AccessContext) -> Workspace | None:
        workspace = self._state.workspaces.get(ctx.workspace_id)
        return workspace if workspace and not workspace.is_deleted else None

    async def create(self, *, name: str, slug: str, created_by_user_id: uuid.UUID) -> Workspace:
        workspace = Workspace(
            id=new_uuid7(),
            name=name,
            slug=slug,
            created_at=datetime.now(UTC),
            version=1,
            created_by_user_id=created_by_user_id,
        )
        self._state.workspaces[workspace.id] = workspace
        return workspace

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[tuple[Workspace, Role]]:
        results = []
        for (workspace_id, member_id), membership in self._state.memberships.items():
            if member_id != user_id:
                continue
            workspace = self._state.workspaces.get(workspace_id)
            if workspace and not workspace.is_deleted:
                results.append((workspace, membership.role))
        return sorted(results, key=lambda pair: pair[0].name)

    async def rename(self, ctx: AccessContext, name: str, *, expected_version: int) -> Workspace:
        workspace = self._state.workspaces.get(ctx.workspace_id)
        if workspace is None or workspace.is_deleted:
            msg = "Workspace not found."
            raise NotFoundError(msg)
        if workspace.version != expected_version:
            msg = "The workspace was modified by someone else. Reload and try again."
            raise ConflictError(msg)
        updated = dataclasses.replace(workspace, name=name, version=workspace.version + 1)
        self._state.workspaces[ctx.workspace_id] = updated
        return updated

    async def soft_delete(self, ctx: AccessContext) -> None:
        workspace = self._state.workspaces.get(ctx.workspace_id)
        if workspace is None or workspace.is_deleted:
            return
        self._state.workspaces[ctx.workspace_id] = dataclasses.replace(
            workspace, deleted_at=datetime.now(UTC), version=workspace.version + 1
        )


class FakeMembershipRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def get(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        return self._state.memberships.get((workspace_id, user_id))

    async def list_members(self, ctx: AccessContext) -> Sequence[Membership]:
        return sorted(
            (m for (ws, _), m in self._state.memberships.items() if ws == ctx.workspace_id),
            key=lambda m: m.created_at,
        )

    async def add(self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role) -> Membership:
        key = (ctx.workspace_id, user_id)
        if key in self._state.memberships:
            msg = "That user is already a member of this workspace."
            raise ConflictError(msg)
        membership = Membership(
            workspace_id=ctx.workspace_id,
            user_id=user_id,
            role=role,
            created_at=datetime.now(UTC),
            invited_by_user_id=ctx.user_id,
        )
        self._state.memberships[key] = membership
        return membership

    async def add_owner(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
        membership = Membership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=Role.OWNER,
            created_at=datetime.now(UTC),
        )
        self._state.memberships[(workspace_id, user_id)] = membership
        return membership

    async def change_role(
        self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role
    ) -> Membership:
        await self._guard_last_owner(ctx, user_id=user_id, new_role=role)
        key = (ctx.workspace_id, user_id)
        current = self._state.memberships.get(key)
        if current is None:
            msg = "That member was not found in this workspace."
            raise NotFoundError(msg)
        updated = dataclasses.replace(current, role=role)
        self._state.memberships[key] = updated
        return updated

    async def remove(self, ctx: AccessContext, *, user_id: uuid.UUID) -> None:
        await self._guard_last_owner(ctx, user_id=user_id, new_role=None)
        key = (ctx.workspace_id, user_id)
        if key not in self._state.memberships:
            msg = "That member was not found in this workspace."
            raise NotFoundError(msg)
        del self._state.memberships[key]

    async def count_owners(self, workspace_id: uuid.UUID) -> int:
        return sum(
            1
            for (ws, _), m in self._state.memberships.items()
            if ws == workspace_id and m.role is Role.OWNER
        )

    async def _guard_last_owner(
        self, ctx: AccessContext, *, user_id: uuid.UUID, new_role: Role | None
    ) -> None:
        if new_role is Role.OWNER:
            return
        current = self._state.memberships.get((ctx.workspace_id, user_id))
        if current is None or current.role is not Role.OWNER:
            return
        if await self.count_owners(ctx.workspace_id) <= 1:
            msg = (
                "This workspace would be left without an owner. "
                "Promote another member to owner first."
            )
            raise ConflictError(msg)


class FakeDocumentRepository:
    def __init__(self, state: _State, *, cursor_secret: str) -> None:
        self._state = state
        self._cursor_secret = cursor_secret

    def _visible(self, ctx: AccessContext) -> list[Document]:
        return [
            self._with_current_version(document)
            for document in self._state.documents.values()
            if document.workspace_id == ctx.workspace_id and not document.is_deleted
        ]

    def _stage_of(self, version: DocumentVersion) -> str | None:
        """Mirrors the SQL adapter: the newest attempt's stage, and only while the
        version is still in the pipeline."""
        if version.status.is_terminal:
            return None
        attempts = [j for j in self._state.jobs.values() if j.document_version_id == version.id]
        if not attempts:
            return None
        newest = max(attempts, key=lambda job: job.attempt)
        return newest.stage.value if newest.stage else None

    def _with_current_version(self, document: Document) -> Document:
        tags = tuple(
            sorted(
                (
                    TagRef(id=tag.id, name=tag.name, color=tag.color)
                    for tag_id, tag in self._state.tags.items()
                    if (document.id, tag_id) in self._state.document_tags
                ),
                key=lambda ref: (ref.name.lower(), ref.id),
            )
        )
        document = dataclasses.replace(document, tags=tags)
        for version in self._state.versions.values():
            if version.document_id == document.id and version.is_current:
                return dataclasses.replace(
                    document,
                    current_version=dataclasses.replace(
                        version, processing_stage=self._stage_of(version)
                    ),
                )
        return document

    async def get(self, ctx: AccessContext, document_id: uuid.UUID) -> Document | None:
        for document in self._visible(ctx):
            if document.id == document_id:
                return document
        return None

    async def find_by_content_hash(
        self, ctx: AccessContext, content_sha256: str
    ) -> Document | None:
        for document in self._visible(ctx):
            if (
                document.current_version
                and document.current_version.content_sha256 == content_sha256
            ):
                return document
        return None

    def _require_live_folder(self, ctx: AccessContext, folder_id: uuid.UUID | None) -> None:
        if folder_id is None:
            return
        folder = self._state.folders.get(folder_id)
        if (
            folder is None
            or folder.workspace_id != ctx.workspace_id
            or folder_id in self._state.deleted_folders
        ):
            msg = "That folder no longer exists."
            raise NotFoundError(msg)

    async def create(
        self,
        ctx: AccessContext,
        *,
        title: str,
        folder_id: uuid.UUID | None,
        content: VersionContent,
        ids: NewDocumentIds | None = None,
    ) -> Document:
        self._require_live_folder(ctx, folder_id)
        if await self.find_by_content_hash(ctx, content.content_sha256) is not None:
            msg = "That file has already been uploaded to this workspace."
            raise ConflictError(msg)
        now = _tick(self._state)
        document = Document(
            id=ids.document_id if ids else new_uuid7(),
            workspace_id=ctx.workspace_id,
            title=title,
            created_at=now,
            updated_at=now,
            version=1,
            folder_id=folder_id,
            created_by_user_id=ctx.user_id,
        )
        version = DocumentVersion(
            id=ids.version_id if ids else new_uuid7(),
            document_id=document.id,
            workspace_id=ctx.workspace_id,
            version_number=1,
            is_current=True,
            storage_key=content.storage_key,
            content_sha256=content.content_sha256,
            byte_size=content.byte_size,
            content_type=content.content_type,
            original_filename=content.original_filename,
            status=ProcessingStatus.PENDING,
            chunk_count=0,
            created_at=now,
            created_by_user_id=ctx.user_id,
        )
        self._state.documents[document.id] = document
        self._state.versions[version.id] = version
        return self._with_current_version(document)

    async def add_version(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        content: VersionContent,
        version_id: uuid.UUID | None = None,
    ) -> DocumentVersion:
        document = await self.get(ctx, document_id)
        if document is None:
            msg = "Document not found."
            raise NotFoundError(msg)
        # Mirrors `uq_document_versions_workspace_content_current`: the
        # constraint is not "no duplicate within this document," it is "no two
        # *current* versions in this workspace share content" -- so a match
        # against a different document's current version is a conflict here
        # too, exactly as it would be against the real database.
        duplicate = await self.find_by_content_hash(ctx, content.content_sha256)
        if duplicate is not None and duplicate.id != document_id:
            msg = "That file has already been uploaded to this workspace."
            raise ConflictError(msg)
        existing = [v for v in self._state.versions.values() if v.document_id == document_id]
        for version in existing:
            if version.is_current:
                self._state.versions[version.id] = dataclasses.replace(version, is_current=False)
        next_number = max((v.version_number for v in existing), default=0) + 1
        new_version = DocumentVersion(
            id=version_id or new_uuid7(),
            document_id=document_id,
            workspace_id=ctx.workspace_id,
            version_number=next_number,
            is_current=True,
            storage_key=content.storage_key,
            content_sha256=content.content_sha256,
            byte_size=content.byte_size,
            content_type=content.content_type,
            original_filename=content.original_filename,
            status=ProcessingStatus.PENDING,
            chunk_count=0,
            created_at=datetime.now(UTC),
            created_by_user_id=ctx.user_id,
        )
        self._state.versions[new_version.id] = new_version
        # Mirrors the SQL adapter: superseded versions lose their chunks.
        for version in existing:
            self._state.chunks.pop(version.id, None)
        return new_version

    async def exists_by_storage_key(self, storage_key: str) -> bool:
        return any(v.storage_key == storage_key for v in self._state.versions.values())

    async def list_versions(
        self, ctx: AccessContext, document_id: uuid.UUID
    ) -> Sequence[DocumentVersion]:
        versions = [
            v
            for v in self._state.versions.values()
            if v.document_id == document_id and v.workspace_id == ctx.workspace_id
        ]
        return sorted(versions, key=lambda v: v.version_number, reverse=True)

    async def page_versions(
        self, ctx: AccessContext, document_id: uuid.UUID, *, before: int | None, limit: int
    ) -> VersionPage:
        if await self.get(ctx, document_id) is None:
            return VersionPage(items=(), next_before=None)
        versions = [
            v
            for v in await self.list_versions(ctx, document_id)
            if before is None or v.version_number < before
        ]
        visible = versions[:limit]
        next_before = visible[-1].version_number if len(versions) > limit and visible else None
        return VersionPage(items=tuple(visible), next_before=next_before)

    async def get_version(
        self, ctx: AccessContext, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> DocumentVersion | None:
        if await self.get(ctx, document_id) is None:
            return None
        version = self._state.versions.get(version_id)
        if version is None or version.document_id != document_id:
            return None
        return version

    async def list_passages(
        self, ctx: AccessContext, version_id: uuid.UUID, *, after: int | None, limit: int
    ) -> PassageSlice:
        version = self._state.versions.get(version_id)
        if version is None or version.workspace_id != ctx.workspace_id:
            return PassageSlice(items=(), preceding_end=None, next_after=None)
        stored = sorted(self._state.chunks.get(version_id, []), key=lambda c: c.ordinal)
        preceding_end = next((c.char_end for c in stored if c.ordinal == after), None)
        rest = [c for c in stored if after is None or c.ordinal > after]
        visible = rest[:limit]
        items = tuple(
            Passage(
                ordinal=c.ordinal,
                text=c.text,
                char_start=c.char_start,
                char_end=c.char_end,
                page_from=c.page_start,
                page_to=c.page_end,
                heading_path=c.heading_path,
            )
            for c in visible
        )
        next_after = items[-1].ordinal if len(rest) > limit and items else None
        return PassageSlice(items=items, preceding_end=preceding_end, next_after=next_after)

    async def set_version_status(
        self, ctx: AccessContext, version_id: uuid.UUID, *, outcome: ProcessingOutcome
    ) -> DocumentVersion:
        version = self._state.versions.get(version_id)
        if version is None or version.workspace_id != ctx.workspace_id:
            msg = "Document version not found."
            raise NotFoundError(msg)
        updated = dataclasses.replace(
            version,
            status=outcome.status,
            chunk_count=(
                outcome.chunk_count if outcome.chunk_count is not None else version.chunk_count
            ),
            page_count=outcome.page_count,
            failure_code=outcome.failure_code,
            failure_reason=outcome.failure_reason,
            processed_at=datetime.now(UTC) if outcome.status.is_terminal else None,
        )
        self._state.versions[version_id] = updated
        return updated

    def _live(self, ctx: AccessContext, document_id: uuid.UUID) -> Document:
        document = self._state.documents.get(document_id)
        if document is None or document.workspace_id != ctx.workspace_id or document.is_deleted:
            msg = "Document not found."
            raise NotFoundError(msg)
        return document

    async def update(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        edit: DocumentEdit,
        expected_version: int,
    ) -> Document:
        if edit.move_to_folder:
            self._require_live_folder(ctx, edit.folder_id)
        document = self._live(ctx, document_id)
        if document.version != expected_version:
            msg = "The document was modified by someone else. Reload and try again."
            raise ConflictError(msg)
        changes: dict[str, object] = {
            "version": document.version + 1,
            "updated_at": _tick(self._state),
        }
        if edit.title is not None:
            changes["title"] = edit.title.strip()
        if edit.move_to_folder:
            changes["folder_id"] = edit.folder_id
        self._state.documents[document_id] = dataclasses.replace(document, **changes)  # type: ignore[arg-type]
        return self._with_current_version(self._state.documents[document_id])

    async def set_archived(
        self, ctx: AccessContext, document_id: uuid.UUID, *, archived: bool
    ) -> Document:
        document = self._live(ctx, document_id)
        if document.is_archived != archived:
            self._state.documents[document_id] = dataclasses.replace(
                document,
                archived_at=_tick(self._state) if archived else None,
                version=document.version + 1,
                updated_at=_tick(self._state),
            )
        return self._with_current_version(self._state.documents[document_id])

    async def soft_delete(self, ctx: AccessContext, document_id: uuid.UUID) -> None:
        document = self._live(ctx, document_id)
        self._state.documents[document_id] = dataclasses.replace(
            document, deleted_at=datetime.now(UTC), version=document.version + 1
        )

    @staticmethod
    def _sort_key(document: Document, sort: DocumentSort) -> object:
        if sort in (DocumentSort.CREATED_DESC, DocumentSort.CREATED_ASC):
            return document.created_at
        if sort is DocumentSort.UPDATED_DESC:
            return document.updated_at
        return document.title.lower()

    async def list_page(
        self,
        ctx: AccessContext,
        *,
        limit: int,
        cursor: str | None = None,
        query: DocumentListQuery | None = None,
    ) -> Page:
        query = query or DocumentListQuery()
        items = self._visible(ctx)

        items = [d for d in items if d.is_archived == (query.archive is ArchiveFilter.ARCHIVED)]
        if query.folder_id is not None:
            items = [d for d in items if d.folder_id == query.folder_id]
        if query.unfiled:
            items = [d for d in items if d.folder_id is None]
        if query.status is not None:
            items = [
                d for d in items if d.current_version and d.current_version.status == query.status
            ]
        for tag_id in set(query.tag_ids):
            items = [d for d in items if any(ref.id == tag_id for ref in d.tags)]
        if (text := query.search_text) is not None:
            items = [d for d in items if text.lower() in d.title.lower()]

        sort = query.sort
        items.sort(
            key=lambda d: (self._sort_key(d, sort), d.id),
            reverse=sort.is_descending,
        )

        if cursor is not None:
            position = SortCursor.decode(cursor, self._cursor_secret, sort=sort.value)
            after = (self._decode(sort, position.value), position.row_id)
            keyed = [(self._sort_key(d, sort), d.id) for d in items]
            items = [
                d
                for d, key in zip(items, keyed, strict=True)
                if (key < after if sort.is_descending else key > after)
            ]

        page_items = items[:limit]
        has_more = len(items) > limit
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            key = self._sort_key(last, sort)
            value = key.astimezone(UTC).isoformat() if isinstance(key, datetime) else str(key)
            next_cursor = SortCursor(sort=sort.value, value=value, row_id=last.id).encode(
                self._cursor_secret
            )
        return Page(items=tuple(page_items), next_cursor=next_cursor)

    @staticmethod
    def _decode(sort: DocumentSort, value: str) -> object:
        if sort in (DocumentSort.TITLE_ASC, DocumentSort.TITLE_DESC):
            return value
        return datetime.fromisoformat(value)


class FakeFolderRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    def _live_folders(self, ctx: AccessContext) -> list[Folder]:
        return [
            folder
            for folder in self._state.folders.values()
            if folder.workspace_id == ctx.workspace_id
            and folder.id not in self._state.deleted_folders
        ]

    def _documents_in(self, ctx: AccessContext, folder_id: uuid.UUID) -> list[Document]:
        return [
            d
            for d in self._state.documents.values()
            if d.workspace_id == ctx.workspace_id and not d.is_deleted and d.folder_id == folder_id
        ]

    async def list_all(self, ctx: AccessContext) -> Sequence[FolderListing]:
        listings = []
        for folder in sorted(
            self._live_folders(ctx), key=lambda f: (f.depth, f.name.lower(), f.id)
        ):
            docs = self._documents_in(ctx, folder.id)
            listings.append(
                FolderListing(
                    folder=folder,
                    document_count=sum(1 for d in docs if not d.is_archived),
                    archived_document_count=sum(1 for d in docs if d.is_archived),
                    child_count=sum(
                        1 for f in self._live_folders(ctx) if f.parent_folder_id == folder.id
                    ),
                )
            )
        return listings

    async def get(self, ctx: AccessContext, folder_id: uuid.UUID) -> Folder | None:
        return next((f for f in self._live_folders(ctx) if f.id == folder_id), None)

    async def count(self, ctx: AccessContext) -> int:
        return len(self._live_folders(ctx))

    def _require_unique(
        self,
        ctx: AccessContext,
        parent_id: uuid.UUID | None,
        name: str,
        *,
        ignore: uuid.UUID | None,
    ) -> None:
        for other in self._live_folders(ctx):
            if (
                other.parent_folder_id == parent_id
                and other.name.lower() == name.lower()
                and other.id != ignore
            ):
                msg = "A folder with that name already exists here."
                raise ConflictError(msg)

    async def create(self, ctx: AccessContext, *, name: str, parent_id: uuid.UUID | None) -> Folder:
        depth = 0
        if parent_id is not None:
            parent = await self.get(ctx, parent_id)
            if parent is None:
                msg = "That folder no longer exists."
                raise NotFoundError(msg)
            depth = parent.depth + 1
            if depth > MAX_FOLDER_DEPTH:
                msg = f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep."
                raise ValidationError(msg)
        self._require_unique(ctx, parent_id, name, ignore=None)
        now = datetime.now(UTC)
        folder = Folder(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            name=name,
            parent_folder_id=parent_id,
            depth=depth,
            version=1,
            created_at=now,
            updated_at=now,
            created_by_user_id=ctx.user_id,
        )
        self._state.folders[folder.id] = folder
        return folder

    async def rename(
        self, ctx: AccessContext, folder_id: uuid.UUID, *, name: str, expected_version: int
    ) -> Folder:
        folder = await self.get(ctx, folder_id)
        if folder is None:
            msg = "That folder no longer exists."
            raise NotFoundError(msg)
        if folder.version != expected_version:
            msg = "The folder was changed by someone else. Reload and try again."
            raise ConflictError(msg)
        self._require_unique(ctx, folder.parent_folder_id, name, ignore=folder_id)
        renamed = dataclasses.replace(
            folder, name=name, version=folder.version + 1, updated_at=datetime.now(UTC)
        )
        self._state.folders[folder_id] = renamed
        return renamed

    async def delete(self, ctx: AccessContext, folder_id: uuid.UUID) -> None:
        if await self.get(ctx, folder_id) is None:
            msg = "That folder no longer exists."
            raise NotFoundError(msg)
        documents = len(self._documents_in(ctx, folder_id))
        children = sum(1 for f in self._live_folders(ctx) if f.parent_folder_id == folder_id)
        if documents or children:
            msg = "This folder isn't empty."
            raise FolderNotEmptyError(msg, documents=documents, folders=children)
        self._state.deleted_folders.add(folder_id)


class FakeTagRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    def _workspace_tags(self, ctx: AccessContext) -> list[Tag]:
        return [t for t in self._state.tags.values() if t.workspace_id == ctx.workspace_id]

    def _live_document(self, ctx: AccessContext, document_id: uuid.UUID) -> Document:
        document = self._state.documents.get(document_id)
        if document is None or document.workspace_id != ctx.workspace_id or document.is_deleted:
            msg = "Document not found."
            raise NotFoundError(msg)
        return document

    def _require_unique(self, ctx: AccessContext, name: str, *, ignore: uuid.UUID | None) -> None:
        for other in self._workspace_tags(ctx):
            if other.name.lower() == name.lower() and other.id != ignore:
                msg = "A tag with that name already exists."
                raise ConflictError(msg)

    async def list_all(self, ctx: AccessContext) -> Sequence[TagListing]:
        def count(tag: Tag) -> int:
            return sum(
                1
                for document_id, tag_id in self._state.document_tags
                if tag_id == tag.id
                and (doc := self._state.documents.get(document_id)) is not None
                and not doc.is_deleted
            )

        return [
            TagListing(tag=tag, document_count=count(tag))
            for tag in sorted(self._workspace_tags(ctx), key=lambda t: (t.name.lower(), t.id))
        ]

    async def get(self, ctx: AccessContext, tag_id: uuid.UUID) -> Tag | None:
        return next((t for t in self._workspace_tags(ctx) if t.id == tag_id), None)

    async def count(self, ctx: AccessContext) -> int:
        return len(self._workspace_tags(ctx))

    async def create(self, ctx: AccessContext, *, name: str, color: str) -> Tag:
        self._require_unique(ctx, name, ignore=None)
        now = datetime.now(UTC)
        tag = Tag(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            name=name,
            color=color,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._state.tags[tag.id] = tag
        return tag

    async def update(
        self,
        ctx: AccessContext,
        tag_id: uuid.UUID,
        *,
        name: str | None,
        color: str | None,
        expected_version: int,
    ) -> Tag:
        tag = await self.get(ctx, tag_id)
        if tag is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg)
        if tag.version != expected_version:
            msg = "The tag was changed by someone else. Reload and try again."
            raise ConflictError(msg)
        if name is not None:
            self._require_unique(ctx, name, ignore=tag_id)
        updated = dataclasses.replace(
            tag,
            name=name if name is not None else tag.name,
            color=color if color is not None else tag.color,
            version=tag.version + 1,
            updated_at=datetime.now(UTC),
        )
        self._state.tags[tag_id] = updated
        return updated

    async def delete(self, ctx: AccessContext, tag_id: uuid.UUID) -> None:
        if await self.get(ctx, tag_id) is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg)
        del self._state.tags[tag_id]
        self._state.document_tags = {
            pair for pair in self._state.document_tags if pair[1] != tag_id
        }

    async def attach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        self._live_document(ctx, document_id)
        if await self.get(ctx, tag_id) is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg)
        pair = (document_id, tag_id)
        if pair in self._state.document_tags:
            return False
        self._state.document_tags.add(pair)
        return True

    async def detach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        self._live_document(ctx, document_id)
        if await self.get(ctx, tag_id) is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg)
        self._state.document_tags.discard((document_id, tag_id))

    async def count_for_document(self, ctx: AccessContext, document_id: uuid.UUID) -> int:
        self._live_document(ctx, document_id)
        return sum(1 for doc_id, _ in self._state.document_tags if doc_id == document_id)


class FakeRefreshTokenRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def create(self, issuance: NewRefreshToken) -> RefreshTokenSession:
        session = RefreshTokenSession(
            id=new_uuid7(),
            user_id=issuance.user_id,
            family_id=issuance.family_id,
            token_hash=issuance.token_hash,
            issued_at=datetime.now(UTC),
            expires_at=issuance.expires_at,
        )
        self._state.refresh_tokens[session.id] = session
        return session

    async def get_by_hash(self, token_hash: str) -> RefreshTokenSession | None:
        for session in self._state.refresh_tokens.values():
            if session.token_hash == token_hash:
                return session
        return None

    async def consume(self, token_id: uuid.UUID) -> None:
        session = self._state.refresh_tokens[token_id]
        self._state.refresh_tokens[token_id] = dataclasses.replace(
            session, consumed_at=datetime.now(UTC)
        )

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        for token_id, session in list(self._state.refresh_tokens.items()):
            if session.family_id == family_id and session.revoked_at is None:
                self._state.refresh_tokens[token_id] = dataclasses.replace(
                    session, revoked_at=datetime.now(UTC)
                )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        for token_id, session in list(self._state.refresh_tokens.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self._state.refresh_tokens[token_id] = dataclasses.replace(
                    session, revoked_at=datetime.now(UTC)
                )


class FakeAccountTokenRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        purpose: AccountTokenPurpose,
        token_hash: str,
        expires_at: datetime,
        client_ip: str | None = None,
    ) -> AccountToken:
        del client_ip  # recorded by the SQL adapter; irrelevant in memory
        token = AccountToken(
            id=new_uuid7(),
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        self._state.account_tokens[token.id] = token
        return token

    async def get_by_hash(
        self, token_hash: str, *, purpose: AccountTokenPurpose
    ) -> AccountToken | None:
        for token in self._state.account_tokens.values():
            if token.token_hash == token_hash and token.purpose is purpose:
                return token
        return None

    async def consume(self, token_id: uuid.UUID) -> bool:
        token = self._state.account_tokens.get(token_id)
        if token is None or token.consumed_at is not None:
            return False
        self._state.account_tokens[token_id] = dataclasses.replace(
            token, consumed_at=datetime.now(UTC)
        )
        return True

    async def invalidate_outstanding(
        self, *, user_id: uuid.UUID, purpose: AccountTokenPurpose
    ) -> None:
        for token_id, token in list(self._state.account_tokens.items()):
            if token.user_id == user_id and token.purpose is purpose and token.consumed_at is None:
                self._state.account_tokens[token_id] = dataclasses.replace(
                    token, consumed_at=datetime.now(UTC)
                )


class FakeAuditRepository:
    """Appends to a list a test can assert against directly."""

    def __init__(self, state: _State) -> None:
        self._state = state

    async def append(self, event: AuditEvent) -> uuid.UUID:
        self._state.audit_events.append(event)
        return new_uuid7()


class FakeUnitOfWork(AbstractAsyncContextManager[UnitOfWorkPort]):
    """Structurally satisfies `domain.ports.unit_of_work.UnitOfWork`.

    Snapshots the whole state on entry and restores it if `commit()` was
    never called -- a coarse but faithful analogue of a real rolled-back
    transaction, which is what lets "a failed operation leaves no partial
    write" be asserted against this fake exactly as it is against the
    database in the integration suite.
    """

    def __init__(self, state: _State, *, cursor_secret: str = "test-secret") -> None:
        self._state = state
        self._committed = False
        self._snapshot: _State | None = None

        self.users = FakeUserRepository(state)
        self.workspaces = FakeWorkspaceRepository(state)
        self.memberships = FakeMembershipRepository(state)
        self.documents = FakeDocumentRepository(state, cursor_secret=cursor_secret)
        self.folders = FakeFolderRepository(state)
        self.tags = FakeTagRepository(state)
        self.refresh_tokens = FakeRefreshTokenRepository(state)
        self.account_tokens = FakeAccountTokenRepository(state)
        self.audit = FakeAuditRepository(state)
        self.processing = FakeProcessingRepository(state)
        self.embeddings = FakeEmbeddingIndexRepository(state)
        self.search = FakeSearchRepository(state)
        self.conversations = FakeConversationRepository(state)

    async def commit(self) -> None:
        self._committed = True

    async def rollback(self) -> None:
        if self._snapshot is not None:
            self._state.__dict__.update(copy.deepcopy(self._snapshot).__dict__)

    async def flush(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        self._snapshot = copy.deepcopy(self._state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            await self.rollback()


class FakeUnitOfWorkFactory:
    """The injectable `UnitOfWorkFactory`. One shared `_State` across every
    `async with` block, exactly like one shared database across every
    transaction in the real factory."""

    def __init__(self, *, cursor_secret: str = "test-secret") -> None:
        self.state = _State()
        self._cursor_secret = cursor_secret

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.state, cursor_secret=self._cursor_secret)
