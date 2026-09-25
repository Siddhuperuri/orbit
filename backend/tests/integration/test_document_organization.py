"""Folders, tags, archive, ordering, and reading -- the SQL, against real PostgreSQL.

The unit suite proves the use-case rules against an in-memory fake. This proves the
parts only PostgreSQL decides: keyset paging under five orderings, `lower()`
collation, `LIKE` escaping, the tag joins and their query count, the partial unique
indexes, and -- the one that cannot be shown any other way -- that deleting a folder
and filing a document into it really do exclude each other under concurrency.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from orbit.domain.access import AccessContext, Role, SystemContext
from orbit.domain.documents import (
    ArchiveFilter,
    DocumentEdit,
    DocumentListQuery,
    DocumentSort,
    Passage,
    without_overlap,
)
from orbit.domain.errors import (
    BadRequestError,
    ConflictError,
    FolderNotEmptyError,
    NotFoundError,
    ValidationError,
)
from orbit.domain.models.entities import Document, ProcessingOutcome, ProcessingStatus
from orbit.domain.processing.content import ChunkDraft, EmbeddedChunk
from orbit.domain.retrieval import LexicalMatch, SearchFilters
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import CURSOR_SECRET, unique_email, unique_slug, version_content

pytestmark = pytest.mark.integration

SYSTEM = SystemContext(reason="integration-test")
PROVIDER = FakeEmbeddingProvider(dimensions=1536)


async def _doc(
    uow: UnitOfWork,
    ctx: AccessContext,
    title: str,
    *,
    folder_id: uuid.UUID | None = None,
) -> Document:
    return await uow.documents.create(
        ctx,
        title=title,
        folder_id=folder_id,
        content=version_content(f"{title}-{uuid.uuid4()}"),
    )


async def _titles(
    uow: UnitOfWork, ctx: AccessContext, query: DocumentListQuery, *, limit: int = 100
) -> list[str]:
    return [d.title for d in (await uow.documents.list_page(ctx, limit=limit, query=query)).items]


async def _walk(
    uow: UnitOfWork, ctx: AccessContext, query: DocumentListQuery, *, page_size: int
) -> list[Document]:
    """Every document a list yields, page by page, following the cursor."""
    seen: list[Document] = []
    cursor: str | None = None
    for _ in range(50):
        page = await uow.documents.list_page(ctx, limit=page_size, cursor=cursor, query=query)
        seen.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            return seen
    pytest.fail("the cursor never ended")


# ---------------------------------------------------------------------------
# Ordering and keyset paging
# ---------------------------------------------------------------------------


class TestOrderingAndPaging:
    @pytest.fixture
    async def library(self, uow: UnitOfWork, ctx: AccessContext) -> list[Document]:
        # Created in this order, so `created_at` ascends -- but every statement in
        # the test transaction shares one `now()`, which is exactly the tie the id
        # tiebreak exists for.
        return [
            await _doc(uow, ctx, title)
            for title in ("delta", "Alpha", "charlie", "Bravo", "echo", "Alpha ")
        ]

    @pytest.mark.parametrize("sort", list(DocumentSort))
    @pytest.mark.parametrize("page_size", [1, 2, 4])
    async def test_paging_visits_every_row_exactly_once_under_every_sort(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        library: list[Document],
        sort: DocumentSort,
        page_size: int,
    ) -> None:
        query = DocumentListQuery(sort=sort)

        paged = await _walk(uow, ctx, query, page_size=page_size)
        whole = (await uow.documents.list_page(ctx, limit=100, query=query)).items

        assert [d.id for d in paged] == [d.id for d in whole]
        assert len({d.id for d in paged}) == len(library)

    async def test_titles_sort_case_insensitively_the_way_a_person_would(
        self, uow: UnitOfWork, ctx: AccessContext, library: list[Document]
    ) -> None:
        titles = await _titles(uow, ctx, DocumentListQuery(sort=DocumentSort.TITLE_ASC))
        # A byte-order sort would put every capital before every lowercase letter.
        assert [t.strip() for t in titles] == [
            "Alpha",
            "Alpha",
            "Bravo",
            "charlie",
            "delta",
            "echo",
        ]

    async def test_title_descending_is_the_exact_reverse_of_ascending(
        self, uow: UnitOfWork, ctx: AccessContext, library: list[Document]
    ) -> None:
        ascending = await _titles(uow, ctx, DocumentListQuery(sort=DocumentSort.TITLE_ASC))
        descending = await _titles(uow, ctx, DocumentListQuery(sort=DocumentSort.TITLE_DESC))
        assert descending == list(reversed(ascending))

    async def test_equal_titles_do_not_lose_or_repeat_a_row_at_a_page_boundary(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """Ten documents with the same title: the boundary falls inside a run of ties,
        so only the id tiebreak keeps the cursor exact."""
        for _ in range(10):
            await _doc(uow, ctx, "Same title")
        query = DocumentListQuery(sort=DocumentSort.TITLE_ASC)

        paged = await _walk(uow, ctx, query, page_size=3)

        assert len(paged) == len({d.id for d in paged}) == 10

    async def test_recently_updated_puts_an_edited_document_first(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        old = await _doc(uow, ctx, "old")
        await _doc(uow, ctx, "newer")
        # `updated_at` is `onupdate=now()`, which is constant inside one transaction,
        # so move it explicitly to make the ordering observable.
        await uow.session.execute(
            text("UPDATE documents SET updated_at = now() + interval '1 hour' WHERE id = :i"),
            {"i": old.id},
        )
        titles = await _titles(uow, ctx, DocumentListQuery(sort=DocumentSort.UPDATED_DESC))
        assert titles[0] == "old"

    async def test_the_cursor_is_bound_to_its_sort(
        self, uow: UnitOfWork, ctx: AccessContext, library: list[Document]
    ) -> None:
        page = await uow.documents.list_page(
            ctx, limit=2, query=DocumentListQuery(sort=DocumentSort.TITLE_ASC)
        )
        assert page.next_cursor is not None
        with pytest.raises(BadRequestError, match="not valid"):
            await uow.documents.list_page(
                ctx,
                limit=2,
                cursor=page.next_cursor,
                query=DocumentListQuery(sort=DocumentSort.CREATED_DESC),
            )

    async def test_a_cursor_from_another_secret_is_refused(
        self, uow: UnitOfWork, ctx: AccessContext, library: list[Document]
    ) -> None:
        page = await uow.documents.list_page(ctx, limit=2)
        assert page.next_cursor is not None
        other = UnitOfWork(uow.session, cursor_secret=CURSOR_SECRET + "-different")
        with pytest.raises(BadRequestError):
            await other.documents.list_page(ctx, limit=2, cursor=page.next_cursor)

    async def test_the_last_page_has_no_cursor(
        self, uow: UnitOfWork, ctx: AccessContext, library: list[Document]
    ) -> None:
        page = await uow.documents.list_page(ctx, limit=len(library))
        assert page.next_cursor is None


class TestFilters:
    async def test_title_text_is_a_case_insensitive_substring(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await _doc(uow, ctx, "Quarterly Budget Review")
        await _doc(uow, ctx, "Meeting notes")
        assert await _titles(uow, ctx, DocumentListQuery(text="BUDGET")) == [
            "Quarterly Budget Review"
        ]

    @pytest.mark.parametrize(
        ("needle", "expected"),
        [
            ("100%", ["Growth 100% target"]),  # `%` is a wildcard, not a literal
            ("q3_plan", ["q3_plan draft"]),  # `_` matches any single character
            ("\\", ["back\\slash"]),  # the escape character itself
        ],
    )
    async def test_wildcard_characters_in_the_filter_are_literals(
        self, uow: UnitOfWork, ctx: AccessContext, needle: str, expected: list[str]
    ) -> None:
        for title in ("Growth 100% target", "Growth 1000 target", "q3_plan draft", "q3xplan draft"):
            await _doc(uow, ctx, title)
        await _doc(uow, ctx, "back\\slash")

        assert await _titles(uow, ctx, DocumentListQuery(text=needle)) == expected

    async def test_folder_and_unfiled(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        folder = await uow.folders.create(ctx, name="Reports", parent_id=None)
        await _doc(uow, ctx, "filed", folder_id=folder.id)
        await _doc(uow, ctx, "loose")

        assert await _titles(uow, ctx, DocumentListQuery(folder_id=folder.id)) == ["filed"]
        assert await _titles(uow, ctx, DocumentListQuery(unfiled=True)) == ["loose"]

    async def test_a_folder_lists_only_its_direct_documents(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        parent = await uow.folders.create(ctx, name="Parent", parent_id=None)
        child = await uow.folders.create(ctx, name="Child", parent_id=parent.id)
        await _doc(uow, ctx, "in parent", folder_id=parent.id)
        await _doc(uow, ctx, "in child", folder_id=child.id)

        assert await _titles(uow, ctx, DocumentListQuery(folder_id=parent.id)) == ["in parent"]

    async def test_by_status(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        ready = await _doc(uow, ctx, "ready one")
        await _doc(uow, ctx, "still pending")
        assert ready.current_version is not None
        await uow.documents.set_version_status(
            ctx, ready.current_version.id, outcome=ProcessingOutcome.ready(chunk_count=2)
        )
        assert await _titles(uow, ctx, DocumentListQuery(status=ProcessingStatus.READY)) == [
            "ready one"
        ]

    async def test_a_document_must_carry_every_selected_tag(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        red = await uow.tags.create(ctx, name="red", color="danger")
        blue = await uow.tags.create(ctx, name="blue", color="accent")
        both = await _doc(uow, ctx, "both")
        only_red = await _doc(uow, ctx, "only red")
        await _doc(uow, ctx, "neither")
        await uow.tags.attach(ctx, both.id, red.id)
        await uow.tags.attach(ctx, both.id, blue.id)
        await uow.tags.attach(ctx, only_red.id, red.id)

        assert sorted(await _titles(uow, ctx, DocumentListQuery(tag_ids=(red.id,)))) == [
            "both",
            "only red",
        ]
        assert await _titles(uow, ctx, DocumentListQuery(tag_ids=(red.id, blue.id))) == ["both"]

    async def test_filters_compose_and_still_page(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="F", parent_id=None)
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        for index in range(7):
            document = await _doc(uow, ctx, f"budget {index}", folder_id=folder.id)
            await uow.tags.attach(ctx, document.id, tag.id)
        await _doc(uow, ctx, "budget elsewhere")
        await _doc(uow, ctx, "other", folder_id=folder.id)

        query = DocumentListQuery(folder_id=folder.id, tag_ids=(tag.id,), text="budget")
        paged = await _walk(uow, ctx, query, page_size=3)

        assert len(paged) == len({d.id for d in paged}) == 7

    async def test_another_tenants_documents_never_appear(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        await _doc(uow, other_ctx, "secret budget")
        assert await _titles(uow, ctx, DocumentListQuery(text="budget")) == []

    async def test_a_tag_from_another_tenant_matches_nothing_here(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        theirs = await uow.tags.create(other_ctx, name="theirs", color="neutral")
        await _doc(uow, ctx, "mine")
        assert await _titles(uow, ctx, DocumentListQuery(tag_ids=(theirs.id,))) == []


class TestTagLoading:
    async def test_a_whole_page_of_tags_costs_one_extra_query_not_one_per_row(
        self, uow: UnitOfWork, ctx: AccessContext, migrated_engine: AsyncEngine
    ) -> None:
        """ADR-0010: an N+1 fails a test rather than becoming an incident."""
        tags = [await uow.tags.create(ctx, name=f"t{i}", color="neutral") for i in range(3)]
        for index in range(20):
            document = await _doc(uow, ctx, f"doc {index:02d}")
            for tag in tags:
                await uow.tags.attach(ctx, document.id, tag.id)
        await uow.flush()

        statements: list[str] = []

        def record(_c: object, _cur: object, statement: str, *_a: object) -> None:
            statements.append(statement)

        sync_engine = uow.session.bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", record)
        try:
            page = await uow.documents.list_page(ctx, limit=20)
        finally:
            event.remove(sync_engine, "before_cursor_execute", record)

        assert len(page.items) == 20
        assert all(len(d.tags) == 3 for d in page.items)
        assert len(statements) == 2, statements  # the page, and every row's tags together

    async def test_tags_come_back_ordered_by_name(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        for name in ("zebra", "Apple", "mango"):
            tag = await uow.tags.create(ctx, name=name, color="neutral")
            await uow.tags.attach(ctx, document.id, tag.id)
        fetched = await uow.documents.get(ctx, document.id)
        assert fetched is not None
        assert [t.name for t in fetched.tags] == ["Apple", "mango", "zebra"]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestArchive:
    async def test_archive_hides_from_the_default_list_and_shows_in_the_archive(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        keep = await _doc(uow, ctx, "keep")
        away = await _doc(uow, ctx, "away")
        await uow.documents.set_archived(ctx, away.id, archived=True)

        assert await _titles(uow, ctx, DocumentListQuery()) == ["keep"]
        archived = DocumentListQuery(archive=ArchiveFilter.ARCHIVED)
        assert await _titles(uow, ctx, archived) == ["away"]
        assert keep.id != away.id

    async def test_an_archived_document_can_still_be_fetched_by_id(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        await uow.documents.set_archived(ctx, document.id, archived=True)
        fetched = await uow.documents.get(ctx, document.id)
        assert fetched is not None
        assert fetched.archived_at is not None

    async def test_both_directions_are_idempotent(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")

        unchanged = await uow.documents.set_archived(ctx, document.id, archived=False)
        assert unchanged.version == document.version

        first = await uow.documents.set_archived(ctx, document.id, archived=True)
        again = await uow.documents.set_archived(ctx, document.id, archived=True)
        assert again.version == first.version == document.version + 1
        assert again.archived_at == first.archived_at

        restored = await uow.documents.set_archived(ctx, document.id, archived=False)
        assert restored.archived_at is None

    async def test_archiving_a_missing_or_foreign_document_is_not_found(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        theirs = await _doc(uow, other_ctx, "theirs")
        with pytest.raises(NotFoundError):
            await uow.documents.set_archived(ctx, theirs.id, archived=True)
        with pytest.raises(NotFoundError):
            await uow.documents.set_archived(ctx, uuid.uuid4(), archived=True)

    async def test_a_deleted_document_cannot_be_archived(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        await uow.documents.soft_delete(ctx, document.id)
        with pytest.raises(NotFoundError):
            await uow.documents.set_archived(ctx, document.id, archived=True)

    async def test_the_archived_view_pages(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        for index in range(7):
            document = await _doc(uow, ctx, f"old {index}")
            await uow.documents.set_archived(ctx, document.id, archived=True)
        await _doc(uow, ctx, "active")

        paged = await _walk(
            uow, ctx, DocumentListQuery(archive=ArchiveFilter.ARCHIVED), page_size=3
        )
        assert len(paged) == 7


async def _index(uow: UnitOfWork, document: Document, passages: Sequence[str]) -> None:
    """Give a document searchable chunks and mark it ready."""
    version = document.current_version
    assert version is not None
    space = PROVIDER.space
    chunks, offset = [], 0
    for ordinal, passage in enumerate(passages):
        draft = ChunkDraft(
            ordinal=ordinal,
            text=passage,
            token_count=max(1, len(passage.split())),
            char_start=offset,
            char_end=offset + len(passage),
            page_start=1,
            page_end=1,
            heading_path=("Doc",),
        )
        offset += len(passage) + 1
        chunks.append(
            EmbeddedChunk(
                draft=draft,
                embedding=PROVIDER.vector(draft.embedding_input),
                embedded_at=datetime.now(UTC),
            )
        )
    await uow.processing.replace_chunks(SYSTEM, version, chunks, space=space, chunker_version="t")
    await uow.processing.transition_version(
        SYSTEM,
        version.id,
        expected=frozenset({ProcessingStatus.PENDING}),
        outcome=ProcessingOutcome.ready(chunk_count=len(chunks)),
    )


class TestArchiveAndRetrieval:
    """Archive is only honest if it takes the document out of answers too."""

    async def test_an_archived_document_is_out_of_search_and_comes_back_on_restore(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "Zeppelin manual")
        await _index(uow, document, ["The zeppelin hangar is on the north field."])
        filters = SearchFilters()

        async def hits() -> list[uuid.UUID]:
            found = await uow.search.lexical_candidates(
                ctx, "zeppelin hangar", match=LexicalMatch.ANY, filters=filters, limit=10
            )
            return [c.chunk_id for c in found]

        assert await hits(), "precondition: the document is searchable"

        await uow.documents.set_archived(ctx, document.id, archived=True)
        assert await hits() == [], "archived, so it must not be retrievable"

        await uow.documents.set_archived(ctx, document.id, archived=False)
        assert await hits(), "restored, so it answers again -- its passages were never removed"

    async def test_archiving_leaves_the_passages_in_place(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        await _index(uow, document, ["one", "two"])
        assert document.current_version is not None

        await uow.documents.set_archived(ctx, document.id, archived=True)

        slice_ = await uow.documents.list_passages(
            ctx, document.current_version.id, after=None, limit=10
        )
        assert [p.text for p in slice_.items] == ["one", "two"]


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


class TestUpdate:
    async def test_it_returns_the_whole_document_including_its_version_and_tags(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """Regression: the old `rename` returned the row without its current version,
        and clients that cached that response showed a healthy document as queued."""
        document = await _doc(uow, ctx, "doc")
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        await uow.tags.attach(ctx, document.id, tag.id)

        updated = await uow.documents.update(
            ctx, document.id, edit=DocumentEdit(title="Renamed"), expected_version=document.version
        )

        assert updated.current_version is not None
        assert [t.name for t in updated.tags] == ["t"]
        assert (updated.title, updated.version) == ("Renamed", document.version + 1)

    async def test_title_and_folder_change_in_one_statement(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="F", parent_id=None)
        document = await _doc(uow, ctx, "doc")

        updated = await uow.documents.update(
            ctx,
            document.id,
            edit=DocumentEdit(title="New", move_to_folder=True, folder_id=folder.id),
            expected_version=document.version,
        )

        assert (updated.title, updated.folder_id) == ("New", folder.id)

    async def test_unfiling_and_title_only_edits(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        folder = await uow.folders.create(ctx, name="F", parent_id=None)
        document = await _doc(uow, ctx, "doc", folder_id=folder.id)

        renamed = await uow.documents.update(
            ctx, document.id, edit=DocumentEdit(title="R"), expected_version=document.version
        )
        assert renamed.folder_id == folder.id, "a title-only edit must leave the folder alone"

        unfiled = await uow.documents.update(
            ctx,
            document.id,
            edit=DocumentEdit(move_to_folder=True, folder_id=None),
            expected_version=renamed.version,
        )
        assert unfiled.folder_id is None
        assert unfiled.title == "R"

    async def test_a_stale_version_conflicts_and_changes_nothing(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        await uow.documents.update(
            ctx, document.id, edit=DocumentEdit(title="First"), expected_version=document.version
        )
        with pytest.raises(ConflictError, match="modified by someone else"):
            await uow.documents.update(
                ctx,
                document.id,
                edit=DocumentEdit(title="Second"),
                expected_version=document.version,
            )
        current = await uow.documents.get(ctx, document.id)
        assert current is not None
        assert current.title == "First"

    async def test_filing_into_a_missing_folder_is_not_found_and_changes_nothing(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "Original")
        with pytest.raises(NotFoundError, match="no longer exists"):
            await uow.documents.update(
                ctx,
                document.id,
                edit=DocumentEdit(title="Renamed", move_to_folder=True, folder_id=uuid.uuid4()),
                expected_version=document.version,
            )
        unchanged = await uow.documents.get(ctx, document.id)
        assert unchanged is not None
        assert unchanged.title == "Original"

    async def test_filing_into_a_deleted_folder_is_refused(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Doomed", parent_id=None)
        await uow.folders.delete(ctx, folder.id)
        document = await _doc(uow, ctx, "doc")

        with pytest.raises(NotFoundError):
            await uow.documents.update(
                ctx,
                document.id,
                edit=DocumentEdit(move_to_folder=True, folder_id=folder.id),
                expected_version=document.version,
            )
        with pytest.raises(NotFoundError):
            await _doc(uow, ctx, "new", folder_id=folder.id)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


class TestFolders:
    async def test_the_tree_is_listed_parents_first_with_counts(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        z = await uow.folders.create(ctx, name="Zebra", parent_id=None)
        a = await uow.folders.create(ctx, name="apple", parent_id=None)
        child = await uow.folders.create(ctx, name="Child", parent_id=z.id)
        await _doc(uow, ctx, "one", folder_id=z.id)
        archived = await _doc(uow, ctx, "two", folder_id=z.id)
        await uow.documents.set_archived(ctx, archived.id, archived=True)

        listing = await uow.folders.list_all(ctx)

        assert [f.folder.name for f in listing] == ["apple", "Zebra", "Child"]
        by_id = {f.folder.id: f for f in listing}
        assert (
            by_id[z.id].document_count,
            by_id[z.id].archived_document_count,
            by_id[z.id].child_count,
        ) == (1, 1, 1)
        assert by_id[child.id].folder.depth == 1
        assert by_id[a.id].is_empty

    async def test_sibling_names_are_unique_case_insensitively(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await uow.folders.create(ctx, name="Reports", parent_id=None)
        with pytest.raises(ConflictError, match="already exists here"):
            await uow.folders.create(ctx, name="REPORTS", parent_id=None)

    async def test_root_folders_cannot_share_a_name(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """`NULLS NOT DISTINCT` on the index: without it every NULL parent counts as
        unique and any number of root folders could share one name."""
        await uow.folders.create(ctx, name="Same", parent_id=None)
        with pytest.raises(ConflictError):
            await uow.folders.create(ctx, name="Same", parent_id=None)

    async def test_the_same_name_is_fine_under_different_parents_and_workspaces(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        a = await uow.folders.create(ctx, name="2025", parent_id=None)
        b = await uow.folders.create(ctx, name="2026", parent_id=None)
        await uow.folders.create(ctx, name="Invoices", parent_id=a.id)
        await uow.folders.create(ctx, name="Invoices", parent_id=b.id)
        await uow.folders.create(other_ctx, name="2025", parent_id=None)

    async def test_nesting_stops_at_the_limit(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        parent: uuid.UUID | None = None
        for level in range(17):
            folder = await uow.folders.create(ctx, name=f"level {level}", parent_id=parent)
            parent = folder.id
        assert folder.depth == 16
        with pytest.raises(ValidationError, match="nested at most"):
            await uow.folders.create(ctx, name="too deep", parent_id=parent)

    async def test_a_parent_in_another_workspace_does_not_exist(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        theirs = await uow.folders.create(other_ctx, name="Theirs", parent_id=None)
        with pytest.raises(NotFoundError):
            await uow.folders.create(ctx, name="Child", parent_id=theirs.id)

    async def test_rename_uses_optimistic_concurrency(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Old", parent_id=None)
        renamed = await uow.folders.rename(
            ctx, folder.id, name="New", expected_version=folder.version
        )
        assert (renamed.name, renamed.version) == ("New", folder.version + 1)
        with pytest.raises(ConflictError, match="changed by someone else"):
            await uow.folders.rename(ctx, folder.id, name="Stale", expected_version=folder.version)

    async def test_renaming_onto_a_sibling_is_a_conflict_with_the_right_message(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await uow.folders.create(ctx, name="Taken", parent_id=None)
        mine = await uow.folders.create(ctx, name="Mine", parent_id=None)
        with pytest.raises(ConflictError, match="already exists here"):
            await uow.folders.rename(ctx, mine.id, name="taken", expected_version=mine.version)

    async def test_another_workspaces_folder_cannot_be_renamed_or_deleted(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        theirs = await uow.folders.create(other_ctx, name="Theirs", parent_id=None)
        with pytest.raises(NotFoundError):
            await uow.folders.rename(ctx, theirs.id, name="Stolen", expected_version=1)
        with pytest.raises(NotFoundError):
            await uow.folders.delete(ctx, theirs.id)
        assert await uow.folders.get(ctx, theirs.id) is None

    async def test_an_empty_folder_is_deleted_and_its_name_becomes_free(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Temp", parent_id=None)
        await uow.folders.delete(ctx, folder.id)

        assert await uow.folders.get(ctx, folder.id) is None
        await uow.folders.create(ctx, name="Temp", parent_id=None)

    async def test_deleting_twice_is_not_found_the_second_time(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Temp", parent_id=None)
        await uow.folders.delete(ctx, folder.id)
        with pytest.raises(NotFoundError):
            await uow.folders.delete(ctx, folder.id)

    async def test_a_folder_holding_documents_or_subfolders_is_refused_with_counts(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Full", parent_id=None)
        await uow.folders.create(ctx, name="Sub", parent_id=folder.id)
        await _doc(uow, ctx, "a", folder_id=folder.id)
        await _doc(uow, ctx, "b", folder_id=folder.id)

        with pytest.raises(FolderNotEmptyError, match="2 documents and 1 subfolder") as raised:
            await uow.folders.delete(ctx, folder.id)

        assert raised.value.context["documents"] == 2
        assert raised.value.context["folders"] == 1
        assert await uow.folders.get(ctx, folder.id) is not None, "nothing was removed"

    async def test_an_archived_document_still_pins_its_folder(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Full", parent_id=None)
        document = await _doc(uow, ctx, "a", folder_id=folder.id)
        await uow.documents.set_archived(ctx, document.id, archived=True)
        with pytest.raises(FolderNotEmptyError):
            await uow.folders.delete(ctx, folder.id)

    async def test_a_deleted_document_no_longer_pins_its_folder(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        folder = await uow.folders.create(ctx, name="Was full", parent_id=None)
        document = await _doc(uow, ctx, "a", folder_id=folder.id)
        await uow.documents.soft_delete(ctx, document.id)
        await uow.folders.delete(ctx, folder.id)

    async def test_the_count_ignores_deleted_folders(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        one = await uow.folders.create(ctx, name="one", parent_id=None)
        await uow.folders.create(ctx, name="two", parent_id=None)
        assert await uow.folders.count(ctx) == 2
        await uow.folders.delete(ctx, one.id)
        assert await uow.folders.count(ctx) == 1


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTags:
    async def test_names_are_unique_case_insensitively_per_workspace(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        await uow.tags.create(ctx, name="Finance", color="neutral")
        # Another workspace may reuse the name. Checked before the conflict: a unique
        # violation aborts the transaction, so nothing can follow it.
        await uow.tags.create(other_ctx, name="Finance", color="neutral")
        with pytest.raises(ConflictError, match="tag with that name"):
            await uow.tags.create(ctx, name="FINANCE", color="accent")

    async def test_the_list_counts_live_documents_only(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        keep = await _doc(uow, ctx, "keep")
        gone = await _doc(uow, ctx, "gone")
        archived = await _doc(uow, ctx, "archived")
        for document in (keep, gone, archived):
            await uow.tags.attach(ctx, document.id, tag.id)
        await uow.documents.soft_delete(ctx, gone.id)
        await uow.documents.set_archived(ctx, archived.id, archived=True)

        (listing,) = await uow.tags.list_all(ctx)

        # The soft-deleted document's join row is left behind, and must not count.
        assert listing.document_count == 2, "the archived one counts: deleting the tag reaches it"

    async def test_update_uses_optimistic_concurrency_and_reports_collisions(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await uow.tags.create(ctx, name="Taken", color="neutral")
        tag = await uow.tags.create(ctx, name="Mine", color="neutral")

        updated = await uow.tags.update(
            ctx, tag.id, name=None, color="danger", expected_version=tag.version
        )
        assert (updated.name, updated.color, updated.version) == ("Mine", "danger", tag.version + 1)

        with pytest.raises(ConflictError, match="changed by someone else"):
            await uow.tags.update(
                ctx, tag.id, name="Other", color=None, expected_version=tag.version
            )
        with pytest.raises(ConflictError, match="tag with that name"):
            await uow.tags.update(
                ctx, tag.id, name="taken", color=None, expected_version=updated.version
            )

    async def test_deleting_removes_the_tag_from_documents_but_not_the_documents(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        tag = await uow.tags.create(ctx, name="Temp", color="neutral")
        document = await _doc(uow, ctx, "doc")
        await uow.tags.attach(ctx, document.id, tag.id)

        await uow.tags.delete(ctx, tag.id)

        fetched = await uow.documents.get(ctx, document.id)
        assert fetched is not None
        assert fetched.tags == ()
        with pytest.raises(NotFoundError):
            await uow.tags.delete(ctx, tag.id)

    async def test_attach_reports_whether_it_added_anything(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        document = await _doc(uow, ctx, "doc")

        assert await uow.tags.attach(ctx, document.id, tag.id) is True
        assert await uow.tags.attach(ctx, document.id, tag.id) is False
        assert await uow.tags.count_for_document(ctx, document.id) == 1

    async def test_detach_is_idempotent(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        document = await _doc(uow, ctx, "doc")
        await uow.tags.detach(ctx, document.id, tag.id)  # never attached
        await uow.tags.attach(ctx, document.id, tag.id)
        await uow.tags.detach(ctx, document.id, tag.id)
        await uow.tags.detach(ctx, document.id, tag.id)
        assert await uow.tags.count_for_document(ctx, document.id) == 0

    async def test_tagging_never_bumps_the_documents_version(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        document = await _doc(uow, ctx, "doc")
        await uow.tags.attach(ctx, document.id, tag.id)
        fetched = await uow.documents.get(ctx, document.id)
        assert fetched is not None
        assert fetched.version == document.version

    async def test_a_tag_and_a_document_from_different_workspaces_cannot_meet(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        mine = await _doc(uow, ctx, "mine")
        theirs = await uow.tags.create(other_ctx, name="theirs", color="neutral")
        with pytest.raises(NotFoundError):
            await uow.tags.attach(ctx, mine.id, theirs.id)

        their_doc = await _doc(uow, other_ctx, "their doc")
        my_tag = await uow.tags.create(ctx, name="mine", color="neutral")
        with pytest.raises(NotFoundError):
            await uow.tags.attach(ctx, their_doc.id, my_tag.id)

    async def test_the_database_rejects_a_cross_tenant_join_even_bypassing_the_repository(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        mine = await _doc(uow, ctx, "mine")
        theirs = await uow.tags.create(other_ctx, name="theirs", color="neutral")
        await uow.flush()
        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "INSERT INTO document_tags (workspace_id, document_id, tag_id)"
                    " VALUES (:w, :d, :t)"
                ),
                {"w": ctx.workspace_id, "d": mine.id, "t": theirs.id},
            )

    async def test_a_deleted_documents_tag_cannot_be_attached(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        tag = await uow.tags.create(ctx, name="t", color="neutral")
        document = await _doc(uow, ctx, "doc")
        await uow.documents.soft_delete(ctx, document.id)
        with pytest.raises(NotFoundError):
            await uow.tags.attach(ctx, document.id, tag.id)


# ---------------------------------------------------------------------------
# Versions, and reading a document
# ---------------------------------------------------------------------------


class TestVersions:
    async def test_history_pages_backwards_and_marks_one_current(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        for index in range(1, 5):
            await uow.documents.add_version(
                ctx, document.id, content=version_content(f"rev-{index}-{uuid.uuid4()}")
            )

        first = await uow.documents.page_versions(ctx, document.id, before=None, limit=2)
        assert [v.version_number for v in first.items] == [5, 4]
        assert [v.is_current for v in first.items] == [True, False]
        assert first.next_before == 4

        second = await uow.documents.page_versions(ctx, document.id, before=4, limit=2)
        assert [v.version_number for v in second.items] == [3, 2]
        last = await uow.documents.page_versions(ctx, document.id, before=2, limit=2)
        assert [v.version_number for v in last.items] == [1]
        assert last.next_before is None

    async def test_a_foreign_or_deleted_documents_history_is_empty(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        theirs = await _doc(uow, other_ctx, "theirs")
        page = await uow.documents.page_versions(ctx, theirs.id, before=None, limit=10)
        assert page.items == ()

        mine = await _doc(uow, ctx, "mine")
        await uow.documents.soft_delete(ctx, mine.id)
        assert (await uow.documents.page_versions(ctx, mine.id, before=None, limit=10)).items == ()

    async def test_get_version_requires_the_right_document_and_workspace(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        mine = await _doc(uow, ctx, "mine")
        other = await _doc(uow, ctx, "other")
        theirs = await _doc(uow, other_ctx, "theirs")
        assert mine.current_version is not None
        assert theirs.current_version is not None

        assert await uow.documents.get_version(ctx, mine.id, mine.current_version.id) is not None
        # Another document's version, addressed through this one.
        assert await uow.documents.get_version(ctx, other.id, mine.current_version.id) is None
        # Another tenant's version.
        assert await uow.documents.get_version(ctx, theirs.id, theirs.current_version.id) is None

    async def test_a_superseded_version_stays_addressable(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await _doc(uow, ctx, "doc")
        assert document.current_version is not None
        first = document.current_version
        await uow.documents.add_version(
            ctx, document.id, content=version_content(f"v2-{uuid.uuid4()}")
        )

        old = await uow.documents.get_version(ctx, document.id, first.id)

        assert old is not None
        assert (old.is_current, old.version_number) == (False, 1)
        assert old.storage_key == first.storage_key, "its file is retained"


class TestPassages:
    TEXT = "AAAA BBBB. CCCC DDDD. EEEE FFFF. GGGG HHHH."

    async def _overlapping(self, uow: UnitOfWork, ctx: AccessContext) -> Document:
        """Passages as the real chunker stores them: each repeats the last sentence of the
        one before, so consecutive character ranges overlap."""
        document = await _doc(uow, ctx, "doc")
        version = document.current_version
        assert version is not None
        spans = [(0, 21), (11, 32), (22, 43)]
        chunks = []
        for ordinal, (start, end) in enumerate(spans):
            draft = ChunkDraft(
                ordinal=ordinal,
                text=self.TEXT[start:end],
                token_count=4,
                char_start=start,
                char_end=end,
                page_start=1,
                page_end=1,
                heading_path=(),
            )
            chunks.append(
                EmbeddedChunk(
                    draft=draft,
                    embedding=PROVIDER.vector(draft.embedding_input),
                    embedded_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        await uow.processing.replace_chunks(
            SYSTEM, version, chunks, space=PROVIDER.space, chunker_version="t"
        )
        return document

    async def test_passages_come_back_in_order_without_their_embeddings(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._overlapping(uow, ctx)
        assert document.current_version is not None

        slice_ = await uow.documents.list_passages(
            ctx, document.current_version.id, after=None, limit=10
        )

        assert [p.ordinal for p in slice_.items] == [0, 1, 2]
        assert slice_.preceding_end is None
        assert slice_.next_after is None
        assert not hasattr(slice_.items[0], "embedding")

    async def test_the_stored_overlap_is_removed_and_the_text_reads_as_written(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """The whole point of the offsets: reading the trimmed passages back yields the
        original text exactly."""
        document = await self._overlapping(uow, ctx)
        assert document.current_version is not None

        slice_ = await uow.documents.list_passages(
            ctx, document.current_version.id, after=None, limit=10
        )
        trimmed = without_overlap(slice_.items, preceding_end=slice_.preceding_end)

        assert " ".join(p.text for p in trimmed) == self.TEXT

    async def test_paging_carries_the_preceding_end_so_the_seam_is_trimmed_too(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._overlapping(uow, ctx)
        assert document.current_version is not None
        version_id = document.current_version.id

        pieces: list[Passage] = []
        after: int | None = None
        for _ in range(5):
            page = await uow.documents.list_passages(ctx, version_id, after=after, limit=1)
            pieces.extend(without_overlap(page.items, preceding_end=page.preceding_end))
            after = page.next_after
            if after is None:
                break

        assert " ".join(p.text for p in pieces) == self.TEXT

    async def test_another_workspaces_version_has_no_passages(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        document = await self._overlapping(uow, ctx)
        assert document.current_version is not None
        slice_ = await uow.documents.list_passages(
            other_ctx, document.current_version.id, after=None, limit=10
        )
        assert slice_.items == ()

    async def test_a_superseded_versions_passages_are_gone(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._overlapping(uow, ctx)
        assert document.current_version is not None
        old_id = document.current_version.id
        await uow.documents.add_version(
            ctx, document.id, content=version_content(f"n-{uuid.uuid4()}")
        )

        assert (await uow.documents.list_passages(ctx, old_id, after=None, limit=10)).items == ()


# ---------------------------------------------------------------------------
# Where the pipeline is
# ---------------------------------------------------------------------------


class TestProcessingStage:
    """The stage a worker last reported, surfaced so "indexing" can be shown as what it
    is rather than guessed from the coarse status."""

    async def _with_job(self, uow: UnitOfWork, ctx: AccessContext) -> Document:
        document = await _doc(uow, ctx, "in flight")
        assert document.current_version is not None
        await uow.processing.create_initial_job(ctx, document.current_version.id, request_id=None)
        return document

    async def _set_stage(self, uow: UnitOfWork, document: Document, stage: str) -> None:
        assert document.current_version is not None
        await uow.session.execute(
            text("UPDATE document_processing_jobs SET stage = :s WHERE document_version_id = :v"),
            {"s": stage, "v": document.current_version.id},
        )

    async def test_an_unclaimed_version_has_no_stage_yet(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._with_job(uow, ctx)
        fetched = await uow.documents.get(ctx, document.id)
        assert fetched is not None and fetched.current_version is not None
        assert fetched.current_version.processing_stage is None

    async def test_the_reported_stage_appears_on_get_and_on_the_list(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._with_job(uow, ctx)
        await self._set_stage(uow, document, "embed")

        fetched = await uow.documents.get(ctx, document.id)
        listed = (await uow.documents.list_page(ctx, limit=10)).items

        assert fetched is not None and fetched.current_version is not None
        assert fetched.current_version.processing_stage == "embed"
        assert listed[0].current_version is not None
        assert listed[0].current_version.processing_stage == "embed"

    async def test_a_finished_version_reports_no_stage_even_though_its_job_still_has_one(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await self._with_job(uow, ctx)
        await self._set_stage(uow, document, "done")
        assert document.current_version is not None
        await uow.documents.set_version_status(
            ctx, document.current_version.id, outcome=ProcessingOutcome.ready(chunk_count=3)
        )

        fetched = await uow.documents.get(ctx, document.id)

        assert fetched is not None and fetched.current_version is not None
        assert fetched.current_version.status is ProcessingStatus.READY
        assert fetched.current_version.processing_stage is None

    async def test_a_page_with_a_mix_costs_the_same_number_of_queries(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """The stage is one correlated subquery inside the page query, not one lookup
        per row (ADR-0010)."""
        for index in range(12):
            document = await _doc(uow, ctx, f"doc {index}")
            if index % 2 == 0:
                assert document.current_version is not None
                await uow.processing.create_initial_job(
                    ctx, document.current_version.id, request_id=None
                )
        await uow.flush()

        statements: list[str] = []

        def record(_c: object, _cur: object, statement: str, *_a: object) -> None:
            statements.append(statement)

        sync_engine = uow.session.bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", record)
        try:
            page = await uow.documents.list_page(ctx, limit=12)
        finally:
            event.remove(sync_engine, "before_cursor_execute", record)

        assert len(page.items) == 12
        assert len(statements) == 2, statements  # the page, and its documents' tags


# ---------------------------------------------------------------------------
# Concurrency: the lock has to actually hold
# ---------------------------------------------------------------------------


@pytest.fixture
async def committed_workspace(migrated_engine: AsyncEngine) -> AsyncIterator[AccessContext]:
    """Real, committed rows -- two connections cannot see each other's uncommitted ones,
    so a concurrency test cannot use the rolled-back fixtures. Cleaned up afterwards."""
    async with AsyncSession(bind=migrated_engine, expire_on_commit=False) as session:
        uow = UnitOfWork(session, cursor_secret=CURSOR_SECRET)
        user = await uow.users.create(
            email=unique_email("race"), password_hash="$argon2id$fake", full_name="Race"
        )
        workspace = await uow.workspaces.create(
            name="Race", slug=unique_slug("race"), created_by_user_id=user.id
        )
        await uow.memberships.add_owner(workspace.id, user.id)
        await uow.commit()
        ctx = AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)
    try:
        yield ctx
    finally:
        async with migrated_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM workspaces WHERE id = :w"), {"w": ctx.workspace_id}
            )
            await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": ctx.user_id})


class TestFolderDeleteVersusFiling:
    """The claim in `SqlFolderRepository.delete`: emptiness cannot be invalidated between
    the check and the commit. That is a statement about two transactions, so it is only
    tested with two.

    The two directions are protected by different things, which mutation-testing the lock
    showed. A document filed *first* is protected by PostgreSQL itself: the foreign key
    takes a `KEY SHARE` lock on the folder row, which `FOR UPDATE` conflicts with. A
    document filed *after* a delete is not: folders are soft-deleted, so the row still
    exists and the foreign key is satisfied. That direction holds only because
    `_lock_live_folder` takes an explicit `FOR SHARE` and then checks `deleted_at`.
    """

    async def test_a_delete_waits_for_a_document_being_filed_and_then_refuses(
        self, migrated_engine: AsyncEngine, committed_workspace: AccessContext
    ) -> None:
        ctx = committed_workspace
        async with AsyncSession(bind=migrated_engine, expire_on_commit=False) as setup:
            seed = UnitOfWork(setup, cursor_secret=CURSOR_SECRET)
            folder = await seed.folders.create(ctx, name="Contested", parent_id=None)
            await seed.commit()

        filing = AsyncSession(bind=migrated_engine, expire_on_commit=False)
        deleting = AsyncSession(bind=migrated_engine, expire_on_commit=False)
        try:
            filer = UnitOfWork(filing, cursor_secret=CURSOR_SECRET)
            deleter = UnitOfWork(deleting, cursor_secret=CURSOR_SECRET)

            # Transaction A files a document into the folder and has not committed yet.
            await filer.documents.create(
                ctx,
                title="Landing",
                folder_id=folder.id,
                content=version_content(f"race-{uuid.uuid4()}"),
            )

            # Transaction B tries to delete the folder. It must *wait* for A: if it
            # returned, it would have checked emptiness without seeing A's document.
            deletion = asyncio.ensure_future(deleter.folders.delete(ctx, folder.id))
            await asyncio.sleep(0.5)
            assert not deletion.done(), "the delete did not wait for the in-flight filing"

            await filer.commit()

            # Now that A's document exists, B's check sees it and refuses.
            with pytest.raises(FolderNotEmptyError):
                await asyncio.wait_for(deletion, timeout=5)
        finally:
            await filing.close()
            await deleting.rollback()
            await deleting.close()

    async def test_a_document_filed_after_the_delete_commits_is_refused(
        self, migrated_engine: AsyncEngine, committed_workspace: AccessContext
    ) -> None:
        ctx = committed_workspace
        async with AsyncSession(bind=migrated_engine, expire_on_commit=False) as setup:
            seed = UnitOfWork(setup, cursor_secret=CURSOR_SECRET)
            folder = await seed.folders.create(ctx, name="Doomed", parent_id=None)
            await seed.commit()

        deleting = AsyncSession(bind=migrated_engine, expire_on_commit=False)
        filing = AsyncSession(bind=migrated_engine, expire_on_commit=False)
        try:
            deleter = UnitOfWork(deleting, cursor_secret=CURSOR_SECRET)
            filer = UnitOfWork(filing, cursor_secret=CURSOR_SECRET)

            await deleter.folders.delete(ctx, folder.id)  # holds the lock, uncommitted

            filing_task = asyncio.ensure_future(
                filer.documents.create(
                    ctx,
                    title="Too late",
                    folder_id=folder.id,
                    content=version_content(f"late-{uuid.uuid4()}"),
                )
            )
            await asyncio.sleep(0.5)
            assert not filing_task.done(), "filing did not wait for the in-flight delete"

            await deleter.commit()

            # The folder is now gone, so the wait ends in a clean refusal -- not a document
            # quietly attached to a deleted folder.
            with pytest.raises(NotFoundError, match="no longer exists"):
                await asyncio.wait_for(filing_task, timeout=5)
        finally:
            await deleting.close()
            await filing.rollback()
            await filing.close()

        async with migrated_engine.connect() as conn:
            attached = await conn.scalar(
                text("SELECT count(*) FROM documents WHERE folder_id = :f"), {"f": folder.id}
            )
        assert attached == 0
