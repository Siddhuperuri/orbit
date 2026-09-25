"""Document repository.

Three operations here carry the real complexity: adding a version, which must
atomically move the "current" marker; listing, which must page in constant time
under five orderings without `OFFSET` and without an N+1 join to each document's
current version or tags; and editing, which must refile a document without ever
pointing it at a folder that is being deleted.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn

from sqlalchemy import (
    Select,
    SQLColumnExpression,
    and_,
    case,
    delete,
    exists,
    func,
    select,
    tuple_,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext
from orbit.domain.documents import (
    ArchiveFilter,
    DocumentEdit,
    DocumentListQuery,
    DocumentSort,
    Passage,
    PassageSlice,
)
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import (
    Document,
    DocumentVersion,
    NewDocumentIds,
    ProcessingOutcome,
    ProcessingStatus,
    TagRef,
    VersionContent,
    VersionPage,
)
from orbit.domain.models.pagination import Page, SortCursor
from orbit.domain.organization import DEFAULT_TAG_COLOR
from orbit.infrastructure.db.errors import flush_translating_conflicts, translate_integrity_error
from orbit.infrastructure.db.models import (
    Chunk as ChunkRow,
)
from orbit.infrastructure.db.models import (
    Document as DocumentRow,
)
from orbit.infrastructure.db.models import (
    DocumentProcessingJob as JobRow,
)
from orbit.infrastructure.db.models import (
    DocumentTag as DocumentTagRow,
)
from orbit.infrastructure.db.models import (
    DocumentVersion as VersionRow,
)
from orbit.infrastructure.db.models import (
    Folder as FolderRow,
)
from orbit.infrastructure.db.models import (
    Tag as TagRow,
)
from orbit.infrastructure.db.models.content import ProcessingStatus as StatusRow


def version_to_entity(row: VersionRow, *, stage: str | None = None) -> DocumentVersion:
    return DocumentVersion(
        id=row.id,
        document_id=row.document_id,
        workspace_id=row.workspace_id,
        version_number=row.version_number,
        is_current=row.is_current,
        storage_key=row.storage_key,
        content_sha256=row.content_sha256,
        byte_size=row.byte_size,
        content_type=row.content_type,
        original_filename=row.original_filename,
        status=ProcessingStatus(row.status.value),
        chunk_count=row.chunk_count,
        created_at=row.created_at,
        failure_code=row.failure_code,
        failure_reason=row.failure_reason,
        page_count=row.page_count,
        language=row.language,
        processed_at=row.processed_at,
        created_by_user_id=row.created_by_user_id,
        processing_stage=stage,
    )


def document_to_entity(
    row: DocumentRow,
    version: VersionRow | None = None,
    *,
    tags: tuple[TagRef, ...] = (),
    stage: str | None = None,
) -> Document:
    return Document(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
        folder_id=row.folder_id,
        created_by_user_id=row.created_by_user_id,
        deleted_at=row.deleted_at,
        archived_at=row.archived_at,
        current_version=version_to_entity(version, stage=stage) if version else None,
        tags=tags,
    )


def _like_pattern(text: str) -> str:
    """A `LIKE` pattern matching `text` as a literal substring.

    `%` and `_` are wildcards, so a title filter for "100%" or "q3_plan" would
    otherwise match far more than was typed.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sort_key(sort: DocumentSort) -> SQLColumnExpression[Any]:
    """The expression a list is ordered by, which is also what its cursor carries."""
    if sort in (DocumentSort.CREATED_DESC, DocumentSort.CREATED_ASC):
        return DocumentRow.created_at
    if sort is DocumentSort.UPDATED_DESC:
        return DocumentRow.updated_at
    return func.lower(DocumentRow.title)


def _encode_key(key: object) -> str:
    if isinstance(key, datetime):
        return key.astimezone(UTC).isoformat()
    return str(key)


def _decode_key(sort: DocumentSort, value: str) -> datetime | str:
    if sort is DocumentSort.TITLE_ASC or sort is DocumentSort.TITLE_DESC:
        return value
    return datetime.fromisoformat(value)


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession, *, cursor_secret: str) -> None:
        self._session = session
        self._cursor_secret = cursor_secret

    # -- reads --------------------------------------------------------------

    def _scoped(self, ctx: AccessContext) -> Select[tuple[DocumentRow, VersionRow, str | None]]:
        """Base query: this workspace, not deleted, joined to its current version.

        The tenant predicate and the soft-delete filter are applied here rather
        than at each call site, so a new query cannot be written without them.
        The join is an outer join because a document exists for the instant
        between its row and its first version being written.

        Archived documents are *included*: a direct link to one must still open
        it (with a banner and a restore button). Only the list filters them, and
        retrieval excludes them separately.
        """
        # The stage of the newest attempt, for versions still in the pipeline. A
        # correlated scalar subquery inside CASE, so it runs only for the (few)
        # in-progress rows of a page and never for the ready majority; it walks
        # `ix_jobs_document_version_id`.
        latest_stage = (
            select(JobRow.stage)
            .where(JobRow.document_version_id == VersionRow.id)
            .order_by(JobRow.attempt.desc())
            .limit(1)
            .correlate(VersionRow)
            .scalar_subquery()
        )
        stage = case(
            (VersionRow.status.in_((StatusRow.PENDING, StatusRow.PROCESSING)), latest_stage),
            else_=None,
        ).label("processing_stage")
        return (
            select(DocumentRow, VersionRow, stage)
            .outerjoin(
                VersionRow,
                (VersionRow.document_id == DocumentRow.id) & VersionRow.is_current,
            )
            .where(
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
            )
        )

    async def _tags_by_document(
        self, ctx: AccessContext, document_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[TagRef, ...]]:
        """Every listed document's tags in one query -- the alternative is one
        query per row of a page (ADR-0010)."""
        if not document_ids:
            return {}
        stmt = (
            select(DocumentTagRow.document_id, TagRow.id, TagRow.name, TagRow.color)
            .join(
                TagRow,
                and_(
                    TagRow.workspace_id == DocumentTagRow.workspace_id,
                    TagRow.id == DocumentTagRow.tag_id,
                ),
            )
            .where(
                DocumentTagRow.workspace_id == ctx.workspace_id,
                DocumentTagRow.document_id.in_(document_ids),
            )
            .order_by(func.lower(TagRow.name), TagRow.id)
        )
        grouped: dict[uuid.UUID, list[TagRef]] = {}
        for document_id, tag_id, name, color in (await self._session.execute(stmt)).all():
            grouped.setdefault(document_id, []).append(
                TagRef(id=tag_id, name=name, color=color or DEFAULT_TAG_COLOR)
            )
        return {document_id: tuple(refs) for document_id, refs in grouped.items()}

    async def _hydrate(
        self,
        ctx: AccessContext,
        rows: Sequence[tuple[DocumentRow, VersionRow | None, str | None]],
    ) -> list[Document]:
        tags = await self._tags_by_document(ctx, [row.id for row, _, _ in rows])
        return [
            document_to_entity(row, version, tags=tags.get(row.id, ()), stage=stage)
            for row, version, stage in rows
        ]

    async def get(self, ctx: AccessContext, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(self._scoped(ctx).where(DocumentRow.id == document_id))
        found = result.first()
        if found is None:
            # Another tenant's document is indistinguishable from one that does
            # not exist, which is the point: a different answer would confirm it
            # exists and leak it across the boundary.
            return None
        row, version, stage = found
        (document,) = await self._hydrate(ctx, [(row, version, stage)])
        return document

    async def find_by_content_hash(
        self, ctx: AccessContext, content_sha256: str
    ) -> Document | None:
        """Deduplication lookup, scoped to the workspace.

        Matches only against *current* versions, mirroring the partial unique
        index. A workspace-wide match across all historical versions would make
        reverting a document to earlier content look like a duplicate upload.
        """
        result = await self._session.execute(
            self._scoped(ctx).where(VersionRow.content_sha256 == content_sha256)
        )
        found = result.first()
        if found is None:
            return None
        row, version, stage = found
        (document,) = await self._hydrate(ctx, [(row, version, stage)])
        return document

    async def list_versions(
        self, ctx: AccessContext, document_id: uuid.UUID
    ) -> Sequence[DocumentVersion]:
        stmt = (
            select(VersionRow)
            .where(
                VersionRow.workspace_id == ctx.workspace_id,
                VersionRow.document_id == document_id,
            )
            .order_by(VersionRow.version_number.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [version_to_entity(row) for row in rows]

    def _versions_of_live_document(
        self, ctx: AccessContext, document_id: uuid.UUID
    ) -> Select[tuple[VersionRow]]:
        return (
            select(VersionRow)
            .join(
                DocumentRow,
                and_(
                    DocumentRow.id == VersionRow.document_id,
                    DocumentRow.workspace_id == VersionRow.workspace_id,
                ),
            )
            .where(
                VersionRow.workspace_id == ctx.workspace_id,
                VersionRow.document_id == document_id,
                DocumentRow.deleted_at.is_(None),
            )
        )

    async def page_versions(
        self, ctx: AccessContext, document_id: uuid.UUID, *, before: int | None, limit: int
    ) -> VersionPage:
        stmt = self._versions_of_live_document(ctx, document_id)
        if before is not None:
            stmt = stmt.where(VersionRow.version_number < before)
        # One extra row tells us whether an older page exists without a COUNT.
        stmt = stmt.order_by(VersionRow.version_number.desc()).limit(limit + 1)

        rows = (await self._session.execute(stmt)).scalars().all()
        visible = rows[:limit]
        items = tuple(version_to_entity(row) for row in visible)
        next_before = items[-1].version_number if len(rows) > limit and items else None
        return VersionPage(items=items, next_before=next_before)

    async def get_version(
        self, ctx: AccessContext, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> DocumentVersion | None:
        row = (
            await self._session.execute(
                self._versions_of_live_document(ctx, document_id).where(VersionRow.id == version_id)
            )
        ).scalar_one_or_none()
        return version_to_entity(row) if row else None

    async def list_passages(
        self, ctx: AccessContext, version_id: uuid.UUID, *, after: int | None, limit: int
    ) -> PassageSlice:
        scope = (
            ChunkRow.workspace_id == ctx.workspace_id,
            ChunkRow.document_version_id == version_id,
        )
        preceding_end: int | None = None
        if after is not None:
            preceding_end = await self._session.scalar(
                select(ChunkRow.char_end).where(*scope, ChunkRow.ordinal == after)
            )

        # Named columns, never the entity: a chunk row carries its 1536-float
        # embedding, and a viewer has no use for six kilobytes per passage.
        stmt = select(
            ChunkRow.ordinal,
            ChunkRow.content,
            ChunkRow.char_start,
            ChunkRow.char_end,
            ChunkRow.page_from,
            ChunkRow.page_to,
            ChunkRow.heading_path,
        ).where(*scope)
        if after is not None:
            stmt = stmt.where(ChunkRow.ordinal > after)
        stmt = stmt.order_by(ChunkRow.ordinal).limit(limit + 1)

        rows = (await self._session.execute(stmt)).all()
        visible = rows[:limit]
        items = tuple(
            Passage(
                ordinal=row.ordinal,
                text=row.content,
                char_start=row.char_start,
                char_end=row.char_end,
                page_from=row.page_from,
                page_to=row.page_to,
                heading_path=row.heading_path,
            )
            for row in visible
        )
        next_after = items[-1].ordinal if len(rows) > limit and items else None
        return PassageSlice(items=items, preceding_end=preceding_end, next_after=next_after)

    def _apply_query(
        self,
        stmt: Select[Any],
        ctx: AccessContext,
        query: DocumentListQuery,
    ) -> Select[Any]:
        if query.archive is ArchiveFilter.ARCHIVED:
            stmt = stmt.where(DocumentRow.archived_at.is_not(None))
        else:
            stmt = stmt.where(DocumentRow.archived_at.is_(None))

        if query.folder_id is not None:
            stmt = stmt.where(DocumentRow.folder_id == query.folder_id)
        if query.unfiled:
            stmt = stmt.where(DocumentRow.folder_id.is_(None))
        if query.status is not None:
            stmt = stmt.where(VersionRow.status == query.status.value)

        # One EXISTS per tag: "has *all* of these" is a conjunction, and EXISTS
        # keeps each condition on the index `ix_document_tags_workspace_id_tag_id`
        # instead of grouping and counting the whole join.
        for tag_id in sorted(set(query.tag_ids)):
            stmt = stmt.where(
                exists().where(
                    DocumentTagRow.workspace_id == ctx.workspace_id,
                    DocumentTagRow.document_id == DocumentRow.id,
                    DocumentTagRow.tag_id == tag_id,
                )
            )

        if (text := query.search_text) is not None:
            stmt = stmt.where(DocumentRow.title.ilike(_like_pattern(text), escape="\\"))
        return stmt

    async def list_page(
        self,
        ctx: AccessContext,
        *,
        limit: int,
        cursor: str | None = None,
        query: DocumentListQuery | None = None,
    ) -> Page:
        """Keyset-paginated list under any of five orderings.

        Every ordering is `(sort key, id)`, matched to an index, so a page costs
        the same whether it is the first or the five-hundredth. The cursor names
        the last row's key *as the database computed it* and the ordering it
        belongs to (`SortCursor`), so it cannot be replayed under another sort.
        """
        query = query or DocumentListQuery()
        sort = query.sort
        key = _sort_key(sort)

        stmt = self._apply_query(self._scoped(ctx), ctx, query).add_columns(key.label("sort_key"))

        if cursor is not None:
            position = SortCursor.decode(cursor, self._cursor_secret, sort=sort.value)
            # Row-value comparison, not `k < x OR (k = x AND id < y)`. PostgreSQL
            # can satisfy this form directly from the composite index; the
            # expanded OR form usually cannot.
            after = (_decode_key(sort, position.value), position.row_id)
            stmt = stmt.where(
                tuple_(key, DocumentRow.id) < after
                if sort.is_descending
                else tuple_(key, DocumentRow.id) > after
            )

        ordering = (
            (key.desc(), DocumentRow.id.desc())
            if sort.is_descending
            else (key.asc(), DocumentRow.id.asc())
        )
        # One extra row is fetched to learn whether another page exists, which
        # avoids a second COUNT query that would double the cost of every list.
        stmt = stmt.order_by(*ordering).limit(limit + 1)

        rows = (await self._session.execute(stmt)).all()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = tuple(
            await self._hydrate(ctx, [(row, version, stage) for row, version, stage, _ in visible])
        )

        next_cursor = None
        if has_more and visible:
            last_document, _, _, last_key = visible[-1]
            next_cursor = SortCursor(
                sort=sort.value, value=_encode_key(last_key), row_id=last_document.id
            ).encode(self._cursor_secret)

        return Page(items=items, next_cursor=next_cursor)

    # -- writes -------------------------------------------------------------

    async def _lock_live_folder(self, ctx: AccessContext, folder_id: uuid.UUID) -> None:
        """Take a shared lock on a folder that must still exist.

        Filing a document takes `FOR SHARE`; deleting a folder takes `FOR
        UPDATE` on the same row (`SqlFolderRepository.delete`). The two cannot
        interleave, so either the document lands first and the delete sees it
        and refuses, or the delete commits first and this raises. Without the
        lock a document could be filed into a folder in the instant after its
        emptiness was checked -- and, because folders are soft-deleted, the
        foreign key would not object.
        """
        found = await self._session.scalar(
            select(FolderRow.id)
            .where(
                FolderRow.id == folder_id,
                FolderRow.workspace_id == ctx.workspace_id,
                FolderRow.deleted_at.is_(None),
            )
            .with_for_update(read=True)
        )
        if found is None:
            msg = "That folder no longer exists."
            raise NotFoundError(msg, folder_id=str(folder_id))

    async def create(
        self,
        ctx: AccessContext,
        *,
        title: str,
        folder_id: uuid.UUID | None,
        content: VersionContent,
        ids: NewDocumentIds | None = None,
    ) -> Document:
        """Create a document and its first version together.

        Both rows are written in the caller's transaction, so a document with no
        version is never committed. The composite foreign key on
        `(workspace_id, folder_id)` means a folder belonging to another tenant
        is rejected by the database rather than by a check here.

        The flush is wrapped rather than left to raise a raw driver error,
        because the upload pipeline is the first caller that can legitimately
        hit `uq_document_versions_workspace_content_current` here: two
        concurrent uploads of identical content can both pass
        `find_by_content_hash` before either commits. Translating locally means
        that race surfaces as the same `ConflictError` a sequential duplicate
        would, rather than as an unhandled `IntegrityError`.
        """
        if folder_id is not None:
            await self._lock_live_folder(ctx, folder_id)

        document = DocumentRow(
            id=ids.document_id if ids else new_uuid7(),
            workspace_id=ctx.workspace_id,
            title=title.strip(),
            folder_id=folder_id,
            created_by_user_id=ctx.user_id,
        )
        self._session.add(document)

        version = VersionRow(
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
            created_by_user_id=ctx.user_id,
        )
        self._session.add(version)

        await flush_translating_conflicts(self._session)

        await self._session.refresh(document)
        await self._session.refresh(version)

        return document_to_entity(document, version)

    async def add_version(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        content: VersionContent,
        version_id: uuid.UUID | None = None,
    ) -> DocumentVersion:
        """Add a revision and make it current.

        Order matters. `uq_document_versions_current` allows at most one current
        version per document, so the previous one must be demoted *before* the
        new one is inserted. Both statements are in the caller's transaction, so
        no reader ever observes zero or two current versions.

        `FOR UPDATE` on the document row serialises two simultaneous uploads to
        the same document; without it both would compute the same next version
        number and one would fail on the unique constraint.
        """
        document = await self._session.get(DocumentRow, document_id, with_for_update=True)
        if document is None or document.workspace_id != ctx.workspace_id or document.deleted_at:
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))

        highest = await self._session.scalar(
            select(func.max(VersionRow.version_number)).where(VersionRow.document_id == document_id)
        )
        next_number = (highest or 0) + 1

        await self._session.execute(
            update(VersionRow)
            .where(VersionRow.document_id == document_id, VersionRow.is_current.is_(True))
            .values(is_current=False)
        )
        # Only the current version has chunks (models/content.py). Deleting the
        # demoted version's chunks here, under the same document lock, is what
        # stops retrieval returning text the document no longer contains. An
        # in-flight job for the demoted version finds it no longer current when
        # it locks the version to index, and records itself superseded.
        await self._session.execute(delete(ChunkRow).where(ChunkRow.document_id == document_id))

        version = VersionRow(
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
            created_by_user_id=ctx.user_id,
        )
        self._session.add(version)
        try:
            await flush_translating_conflicts(self._session)
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        await self._session.refresh(version)
        return version_to_entity(version)

    async def exists_by_storage_key(self, storage_key: str) -> bool:
        """Whether any version, in any workspace, still references this key.

        Not workspace-scoped -- see the port docstring. Backed by
        `ix_document_versions_storage_key`, which exists for exactly this
        lookup (`infrastructure/db/models/content.py`).
        """
        stmt = (
            select(func.count())
            .select_from(VersionRow)
            .where(VersionRow.storage_key == storage_key)
        )
        count = await self._session.scalar(stmt)
        return bool(count)

    async def set_version_status(
        self, ctx: AccessContext, version_id: uuid.UUID, *, outcome: ProcessingOutcome
    ) -> DocumentVersion:
        """Move a version through the pipeline.

        `processed_at` is set exactly when the status becomes terminal, because a
        check constraint requires the two to agree -- so an inconsistent write is
        rejected by the database rather than producing a version that claims to
        be ready but was never processed.
        """
        values: dict[str, object] = {
            "status": outcome.status.value,
            "failure_code": outcome.failure_code,
            "failure_reason": outcome.failure_reason,
        }
        if outcome.chunk_count is not None:
            values["chunk_count"] = outcome.chunk_count
        if outcome.page_count is not None:
            values["page_count"] = outcome.page_count
        values["processed_at"] = func.now() if outcome.status.is_terminal else None

        stmt = (
            update(VersionRow)
            .where(VersionRow.id == version_id, VersionRow.workspace_id == ctx.workspace_id)
            .values(**values)
            .returning(VersionRow)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            msg = "Document version not found."
            raise NotFoundError(msg, version_id=str(version_id))
        return version_to_entity(row)

    async def _reload(self, ctx: AccessContext, document_id: uuid.UUID) -> Document:
        """The document as every reader will now see it.

        Re-read rather than assembled from the `UPDATE ... RETURNING` row: that
        row has no current version and no tags, and a caller that replaces its
        copy with a response missing them shows a healthy document as "queued"
        until something refetches it.
        """
        document = await self.get(ctx, document_id)
        if document is None:  # pragma: no cover -- defensive; the write just matched it
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))
        return document

    async def update(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        edit: DocumentEdit,
        expected_version: int,
    ) -> Document:
        if edit.move_to_folder and edit.folder_id is not None:
            await self._lock_live_folder(ctx, edit.folder_id)

        values: dict[str, object] = {"version": DocumentRow.version + 1}
        if edit.title is not None:
            values["title"] = edit.title.strip()
        if edit.move_to_folder:
            values["folder_id"] = edit.folder_id

        stmt = (
            update(DocumentRow)
            .where(
                DocumentRow.id == document_id,
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
                DocumentRow.version == expected_version,
            )
            .values(**values)
            .returning(DocumentRow.id)
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is None:
            await self._raise_missing_or_conflicting(ctx, document_id)
        return await self._reload(ctx, document_id)

    async def set_archived(
        self, ctx: AccessContext, document_id: uuid.UUID, *, archived: bool
    ) -> Document:
        stmt = (
            update(DocumentRow)
            .where(
                DocumentRow.id == document_id,
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
                DocumentRow.archived_at.is_(None)
                if archived
                else DocumentRow.archived_at.is_not(None),
            )
            .values(
                archived_at=func.now() if archived else None,
                version=DocumentRow.version + 1,
            )
            .returning(DocumentRow.id)
        )
        # No row matched: either the document is gone, or it is already in the
        # state asked for. `_reload` tells the two apart, and the second is a
        # success -- the request's postcondition already holds.
        await self._session.execute(stmt)
        return await self._reload(ctx, document_id)

    async def soft_delete(self, ctx: AccessContext, document_id: uuid.UUID) -> None:
        """Make the document invisible immediately.

        Chunks, embeddings, and the stored object are reclaimed asynchronously
        (docs/architecture/data-flow.md): the user's expectation is satisfied at
        this commit, and reclamation is idempotent and retryable.
        """
        stmt = (
            update(DocumentRow)
            .where(
                DocumentRow.id == document_id,
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), version=DocumentRow.version + 1)
        )
        deleted = (await self._session.execute(stmt.returning(DocumentRow.id))).scalar_one_or_none()
        if deleted is None:
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))

    async def _raise_missing_or_conflicting(
        self, ctx: AccessContext, document_id: uuid.UUID
    ) -> NoReturn:
        exists_ = await self._session.scalar(
            select(func.count())
            .select_from(DocumentRow)
            .where(
                DocumentRow.id == document_id,
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        if exists_:
            msg = "The document was modified by someone else. Reload and try again."
            raise ConflictError(msg, document_id=str(document_id))
        msg = "Document not found."
        raise NotFoundError(msg, document_id=str(document_id))
