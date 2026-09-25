"""Folders, tags, archive, filing, listing, versions, and reading a document.

Run against the in-memory unit of work, so these pin the *use-case* rules --
who may do what, what counts as a conflict, what is idempotent. The SQL that
implements the same behaviour is exercised against PostgreSQL in
`tests/integration/test_document_organization.py`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from orbit.application.documents.archive_document import ArchiveDocument
from orbit.application.documents.get_document_download import GetDocumentDownload
from orbit.application.documents.list_document_versions import ListDocumentVersions
from orbit.application.documents.list_documents import ListDocuments
from orbit.application.documents.read_document_content import ReadDocumentContent
from orbit.application.documents.tag_document import AddDocumentTag, RemoveDocumentTag
from orbit.application.documents.update_document import UpdateDocument
from orbit.application.folders import manage as folders_module
from orbit.application.folders.manage import CreateFolder, DeleteFolder, ListFolders, RenameFolder
from orbit.application.tags import manage as tags_module
from orbit.application.tags.manage import CreateTag, DeleteTag, ListTags, UpdateTag
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import ArchiveFilter, DocumentEdit, DocumentListQuery, DocumentSort
from orbit.domain.errors import (
    BadRequestError,
    ConflictError,
    FolderNotEmptyError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from orbit.domain.models.entities import (
    Document,
    ProcessingOutcome,
    ProcessingStatus,
    VersionContent,
)
from orbit.domain.organization import MAX_FOLDER_DEPTH, MAX_TAGS_PER_DOCUMENT
from tests.unit.fakes.fake_processing import StoredChunk
from tests.unit.fakes.fake_storage import FakeObjectStorage
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


@pytest.fixture
async def owner_id(uow_factory: FakeUnitOfWorkFactory) -> uuid.UUID:
    user = await uow_factory().users.create(
        email="owner@example.com", password_hash="h", full_name="Owner"
    )
    return user.id


@pytest.fixture
async def workspace_id(uow_factory: FakeUnitOfWorkFactory, owner_id: uuid.UUID) -> uuid.UUID:
    return (
        await CreateWorkspace(uow_factory).execute(name="Research", created_by_user_id=owner_id)
    ).id


def ctx_for(workspace_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> AccessContext:
    return AccessContext(user_id=user_id, workspace_id=workspace_id, role=role)


@pytest.fixture
def owner(workspace_id: uuid.UUID, owner_id: uuid.UUID) -> AccessContext:
    return ctx_for(workspace_id, owner_id, Role.OWNER)


@pytest.fixture
def viewer(workspace_id: uuid.UUID, owner_id: uuid.UUID) -> AccessContext:
    return ctx_for(workspace_id, owner_id, Role.VIEWER)


@pytest.fixture
def member(workspace_id: uuid.UUID, owner_id: uuid.UUID) -> AccessContext:
    return ctx_for(workspace_id, owner_id, Role.MEMBER)


@pytest.fixture
def stranger(owner_id: uuid.UUID) -> AccessContext:
    """Someone in a different workspace entirely."""
    return ctx_for(uuid.uuid4(), owner_id, Role.OWNER)


def content(marker: str) -> VersionContent:
    digest = hashlib.sha256(marker.encode()).hexdigest()
    return VersionContent(
        storage_key=f"workspaces/test/documents/test/{digest}",
        content_sha256=digest,
        byte_size=1024,
        content_type="application/pdf",
        original_filename=f"{marker}.pdf",
    )


async def add_document(
    uow_factory: FakeUnitOfWorkFactory,
    ctx: AccessContext,
    title: str,
    *,
    folder_id: uuid.UUID | None = None,
) -> Document:
    return await uow_factory().documents.create(
        ctx, title=title, folder_id=folder_id, content=content(f"{title}-{uuid.uuid4()}")
    )


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


class TestFolderPermissions:
    async def test_a_viewer_can_look_but_not_change(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Reports", parent_id=None)

        assert len(await ListFolders(uow_factory).execute(viewer)) == 1
        with pytest.raises(PermissionDeniedError):
            await CreateFolder(uow_factory).execute(viewer, name="Mine", parent_id=None)
        with pytest.raises(PermissionDeniedError):
            await RenameFolder(uow_factory).execute(
                viewer, folder.id, name="Renamed", expected_version=folder.version
            )
        with pytest.raises(PermissionDeniedError):
            await DeleteFolder(uow_factory).execute(viewer, folder.id)

    async def test_a_member_can_organise(
        self, uow_factory: FakeUnitOfWorkFactory, member: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(member, name="Reports", parent_id=None)
        assert folder.name == "Reports"


class TestFolderTree:
    async def test_children_are_listed_after_their_parents(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        z = await create.execute(owner, name="Zebra", parent_id=None)
        a = await create.execute(owner, name="Apple", parent_id=None)
        child = await create.execute(owner, name="Aardvark", parent_id=z.id)

        listing = await ListFolders(uow_factory).execute(owner)

        assert [item.folder.name for item in listing] == ["Apple", "Zebra", "Aardvark"]
        assert child.depth == 1
        assert {item.folder.id: item.child_count for item in listing} == {
            a.id: 0,
            z.id: 1,
            child.id: 0,
        }

    async def test_sibling_names_are_unique_ignoring_case_and_spacing(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        await create.execute(owner, name="Q3 Plan", parent_id=None)
        with pytest.raises(ConflictError, match="already exists"):
            await create.execute(owner, name="  q3    PLAN ", parent_id=None)

    async def test_the_same_name_is_fine_under_different_parents(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        a = await create.execute(owner, name="2025", parent_id=None)
        b = await create.execute(owner, name="2026", parent_id=None)
        await create.execute(owner, name="Invoices", parent_id=a.id)
        await create.execute(owner, name="Invoices", parent_id=b.id)

    async def test_a_missing_parent_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        with pytest.raises(NotFoundError):
            await CreateFolder(uow_factory).execute(owner, name="Orphan", parent_id=uuid.uuid4())

    async def test_nesting_is_bounded(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        parent: uuid.UUID | None = None
        for depth in range(MAX_FOLDER_DEPTH + 1):
            folder = await create.execute(owner, name=f"level-{depth}", parent_id=parent)
            parent = folder.id
        with pytest.raises(ValidationError, match="nested at most"):
            await create.execute(owner, name="too deep", parent_id=parent)

    async def test_the_number_of_folders_is_bounded(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        owner: AccessContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(folders_module, "MAX_FOLDERS_PER_WORKSPACE", 2)
        create = CreateFolder(uow_factory)
        await create.execute(owner, name="one", parent_id=None)
        await create.execute(owner, name="two", parent_id=None)
        with pytest.raises(ValidationError, match="at most 2 folders"):
            await create.execute(owner, name="three", parent_id=None)

    async def test_a_blank_name_is_refused(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        with pytest.raises(ValidationError):
            await CreateFolder(uow_factory).execute(owner, name="   ", parent_id=None)


class TestFolderRename:
    async def test_renaming_bumps_the_version(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Old", parent_id=None)
        renamed = await RenameFolder(uow_factory).execute(
            owner, folder.id, name="New", expected_version=folder.version
        )
        assert (renamed.name, renamed.version) == ("New", folder.version + 1)

    async def test_a_stale_version_is_a_conflict_not_an_overwrite(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        rename = RenameFolder(uow_factory)
        folder = await CreateFolder(uow_factory).execute(owner, name="Old", parent_id=None)
        await rename.execute(owner, folder.id, name="First", expected_version=folder.version)
        with pytest.raises(ConflictError, match="changed by someone else"):
            await rename.execute(owner, folder.id, name="Second", expected_version=folder.version)

    async def test_renaming_onto_a_sibling_is_a_conflict(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        await create.execute(owner, name="Taken", parent_id=None)
        other = await create.execute(owner, name="Mine", parent_id=None)
        with pytest.raises(ConflictError, match="already exists"):
            await RenameFolder(uow_factory).execute(
                owner, other.id, name="taken", expected_version=other.version
            )

    async def test_keeping_the_same_name_is_not_a_collision_with_itself(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Same", parent_id=None)
        renamed = await RenameFolder(uow_factory).execute(
            owner, folder.id, name="SAME", expected_version=folder.version
        )
        assert renamed.name == "SAME"


class TestFolderDelete:
    async def test_an_empty_folder_can_be_deleted_and_its_name_reused(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        folder = await create.execute(owner, name="Temp", parent_id=None)
        await DeleteFolder(uow_factory).execute(owner, folder.id)

        assert await ListFolders(uow_factory).execute(owner) == []
        await create.execute(owner, name="Temp", parent_id=None)

    async def test_a_folder_holding_documents_is_refused_with_the_counts(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Full", parent_id=None)
        await add_document(uow_factory, owner, "a", folder_id=folder.id)
        await add_document(uow_factory, owner, "b", folder_id=folder.id)

        with pytest.raises(FolderNotEmptyError) as raised:
            await DeleteFolder(uow_factory).execute(owner, folder.id)

        assert raised.value.context["documents"] == 2
        # Nothing was removed on the caller's behalf.
        assert len(await ListFolders(uow_factory).execute(owner)) == 1

    async def test_an_archived_document_still_pins_its_folder(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Full", parent_id=None)
        document = await add_document(uow_factory, owner, "a", folder_id=folder.id)
        await ArchiveDocument(uow_factory).execute(owner, document.id, archived=True)

        with pytest.raises(FolderNotEmptyError):
            await DeleteFolder(uow_factory).execute(owner, folder.id)

        listing = (await ListFolders(uow_factory).execute(owner))[0]
        assert (listing.document_count, listing.archived_document_count) == (0, 1)

    async def test_a_folder_with_subfolders_is_refused(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateFolder(uow_factory)
        parent = await create.execute(owner, name="Parent", parent_id=None)
        await create.execute(owner, name="Child", parent_id=parent.id)
        with pytest.raises(FolderNotEmptyError) as raised:
            await DeleteFolder(uow_factory).execute(owner, parent.id)
        assert raised.value.context["folders"] == 1

    async def test_deleting_a_folder_that_is_already_gone_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        with pytest.raises(NotFoundError):
            await DeleteFolder(uow_factory).execute(owner, uuid.uuid4())


class TestFolderTenancy:
    async def test_another_workspaces_folder_does_not_exist(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Mine", parent_id=None)

        assert await ListFolders(uow_factory).execute(stranger) == []
        with pytest.raises(NotFoundError):
            await RenameFolder(uow_factory).execute(
                stranger, folder.id, name="Stolen", expected_version=folder.version
            )
        with pytest.raises(NotFoundError):
            await DeleteFolder(uow_factory).execute(stranger, folder.id)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTagPermissions:
    async def test_a_viewer_can_read_tags_but_not_manage_or_apply_them(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Urgent", color=None)
        document = await add_document(uow_factory, owner, "doc")

        assert len(await ListTags(uow_factory).execute(viewer)) == 1
        with pytest.raises(PermissionDeniedError):
            await CreateTag(uow_factory).execute(viewer, name="New", color=None)
        with pytest.raises(PermissionDeniedError):
            await UpdateTag(uow_factory).execute(
                viewer, tag.id, name="X", color=None, expected_version=tag.version
            )
        with pytest.raises(PermissionDeniedError):
            await DeleteTag(uow_factory).execute(viewer, tag.id)
        with pytest.raises(PermissionDeniedError):
            await AddDocumentTag(uow_factory).execute(viewer, document.id, tag.id)
        with pytest.raises(PermissionDeniedError):
            await RemoveDocumentTag(uow_factory).execute(viewer, document.id, tag.id)


class TestTagLifecycle:
    async def test_the_default_colour_is_neutral(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Plain", color=None)
        assert tag.color == "neutral"

    async def test_an_unknown_colour_is_refused(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        with pytest.raises(ValidationError, match="one of"):
            await CreateTag(uow_factory).execute(owner, name="Odd", color="chartreuse")

    async def test_names_are_unique_ignoring_case(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateTag(uow_factory)
        await create.execute(owner, name="Finance", color=None)
        with pytest.raises(ConflictError, match="already exists"):
            await create.execute(owner, name="  FINANCE ", color=None)

    async def test_the_number_of_tags_is_bounded(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        owner: AccessContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tags_module, "MAX_TAGS_PER_WORKSPACE", 1)
        create = CreateTag(uow_factory)
        await create.execute(owner, name="one", color=None)
        with pytest.raises(ValidationError, match="at most 1 tags"):
            await create.execute(owner, name="two", color=None)

    async def test_update_needs_something_to_change(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Tag", color=None)
        with pytest.raises(ValidationError, match="Nothing to change"):
            await UpdateTag(uow_factory).execute(
                owner, tag.id, name=None, color=None, expected_version=tag.version
            )

    async def test_a_stale_update_is_a_conflict(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        update = UpdateTag(uow_factory)
        tag = await CreateTag(uow_factory).execute(owner, name="Tag", color=None)
        await update.execute(owner, tag.id, name="First", color=None, expected_version=tag.version)
        with pytest.raises(ConflictError, match="changed by someone else"):
            await update.execute(
                owner, tag.id, name=None, color="danger", expected_version=tag.version
            )

    async def test_recolouring_keeps_the_name(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Keep", color=None)
        updated = await UpdateTag(uow_factory).execute(
            owner, tag.id, name=None, color="warning", expected_version=tag.version
        )
        assert (updated.name, updated.color) == ("Keep", "warning")

    async def test_deleting_a_tag_removes_it_from_every_document(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Temp", color=None)
        document = await add_document(uow_factory, owner, "doc")
        await AddDocumentTag(uow_factory).execute(owner, document.id, tag.id)

        listing = (await ListTags(uow_factory).execute(owner))[0]
        assert listing.document_count == 1

        await DeleteTag(uow_factory).execute(owner, tag.id)

        reloaded = await uow_factory().documents.get(owner, document.id)
        assert reloaded is not None
        assert reloaded.tags == ()


class TestTaggingDocuments:
    async def test_adding_a_tag_twice_is_the_same_as_once(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Once", color=None)
        document = await add_document(uow_factory, owner, "doc")
        add = AddDocumentTag(uow_factory)

        await add.execute(owner, document.id, tag.id)
        again = await add.execute(owner, document.id, tag.id)

        assert [ref.name for ref in again.tags] == ["Once"]

    async def test_two_people_tagging_at_once_both_keep_their_tag(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """The reason tagging is add/remove rather than "replace the list": neither
        write can erase the other."""
        first = await CreateTag(uow_factory).execute(owner, name="First", color=None)
        second = await CreateTag(uow_factory).execute(owner, name="Second", color=None)
        document = await add_document(uow_factory, owner, "doc")
        add = AddDocumentTag(uow_factory)

        await add.execute(owner, document.id, first.id)
        result = await add.execute(owner, document.id, second.id)

        assert [ref.name for ref in result.tags] == ["First", "Second"]

    async def test_removing_an_absent_tag_succeeds(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Never", color=None)
        document = await add_document(uow_factory, owner, "doc")
        result = await RemoveDocumentTag(uow_factory).execute(owner, document.id, tag.id)
        assert result.tags == ()

    async def test_tagging_does_not_change_the_documents_own_version(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """Tagging commutes with everything else, so it must not make a concurrent
        rename fail as a conflict."""
        tag = await CreateTag(uow_factory).execute(owner, name="Quiet", color=None)
        document = await add_document(uow_factory, owner, "doc")
        tagged = await AddDocumentTag(uow_factory).execute(owner, document.id, tag.id)
        assert tagged.version == document.version

    async def test_a_document_carries_a_bounded_number_of_tags(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateTag(uow_factory)
        document = await add_document(uow_factory, owner, "doc")
        add = AddDocumentTag(uow_factory)
        for index in range(MAX_TAGS_PER_DOCUMENT):
            tag = await create.execute(owner, name=f"tag-{index}", color=None)
            await add.execute(owner, document.id, tag.id)

        extra = await create.execute(owner, name="one too many", color=None)
        with pytest.raises(ValidationError, match="at most"):
            await add.execute(owner, document.id, extra.id)

        # The refused insert was rolled back, not left half-applied.
        reloaded = await uow_factory().documents.get(owner, document.id)
        assert reloaded is not None
        assert len(reloaded.tags) == MAX_TAGS_PER_DOCUMENT

    async def test_re_adding_at_the_limit_is_still_fine(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create = CreateTag(uow_factory)
        document = await add_document(uow_factory, owner, "doc")
        add = AddDocumentTag(uow_factory)
        tag = await create.execute(owner, name="tag-0", color=None)
        for index in range(1, MAX_TAGS_PER_DOCUMENT):
            other = await create.execute(owner, name=f"tag-{index}", color=None)
            await add.execute(owner, document.id, other.id)
        await add.execute(owner, document.id, tag.id)

        await add.execute(owner, document.id, tag.id)  # idempotent: must not trip the limit

    async def test_another_workspaces_tag_or_document_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        tag = await CreateTag(uow_factory).execute(owner, name="Mine", color=None)
        document = await add_document(uow_factory, owner, "doc")

        with pytest.raises(NotFoundError):
            await AddDocumentTag(uow_factory).execute(stranger, document.id, tag.id)
        theirs = await CreateTag(uow_factory).execute(stranger, name="Theirs", color=None)
        with pytest.raises(NotFoundError):
            await AddDocumentTag(uow_factory).execute(owner, document.id, theirs.id)


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestArchive:
    async def test_archiving_takes_a_document_out_of_the_default_list(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        keep = await add_document(uow_factory, owner, "keep")
        away = await add_document(uow_factory, owner, "away")
        await ArchiveDocument(uow_factory).execute(owner, away.id, archived=True)

        active = await ListDocuments(uow_factory).execute(owner, limit=25)
        archived = await ListDocuments(uow_factory).execute(
            owner, limit=25, query=DocumentListQuery(archive=ArchiveFilter.ARCHIVED)
        )

        assert [d.id for d in active.items] == [keep.id]
        assert [d.id for d in archived.items] == [away.id]

    async def test_an_archived_document_can_still_be_opened_by_id(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """A link someone saved must not 404 just because the document was filed away."""
        document = await add_document(uow_factory, owner, "doc")
        archived = await ArchiveDocument(uow_factory).execute(owner, document.id, archived=True)
        assert archived.is_archived
        fetched = await uow_factory().documents.get(owner, document.id)
        assert fetched is not None and fetched.is_archived

    async def test_restoring_brings_it_back(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        archive = ArchiveDocument(uow_factory)
        await archive.execute(owner, document.id, archived=True)
        restored = await archive.execute(owner, document.id, archived=False)

        assert not restored.is_archived
        page = await ListDocuments(uow_factory).execute(owner, limit=25)
        assert [d.id for d in page.items] == [document.id]

    async def test_both_directions_are_idempotent_and_do_not_churn_the_version(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        archive = ArchiveDocument(uow_factory)

        untouched = await archive.execute(owner, document.id, archived=False)
        assert untouched.version == document.version

        first = await archive.execute(owner, document.id, archived=True)
        second = await archive.execute(owner, document.id, archived=True)
        assert second.version == first.version == document.version + 1

    async def test_archiving_bumps_the_version_so_a_stale_edit_is_caught(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        await ArchiveDocument(uow_factory).execute(owner, document.id, archived=True)
        with pytest.raises(ConflictError):
            await UpdateDocument(uow_factory).execute(
                owner,
                document.id,
                edit=DocumentEdit(title="Stale"),
                expected_version=document.version,
            )

    async def test_a_viewer_cannot_archive_or_restore(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        for archived in (True, False):
            with pytest.raises(PermissionDeniedError):
                await ArchiveDocument(uow_factory).execute(viewer, document.id, archived=archived)

    async def test_a_missing_or_foreign_document_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        with pytest.raises(NotFoundError):
            await ArchiveDocument(uow_factory).execute(stranger, document.id, archived=True)
        with pytest.raises(NotFoundError):
            await ArchiveDocument(uow_factory).execute(owner, uuid.uuid4(), archived=True)


# ---------------------------------------------------------------------------
# Renaming and filing
# ---------------------------------------------------------------------------


class TestUpdateDocument:
    async def test_the_response_is_the_whole_document_not_a_fragment(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """A client replaces its cached copy with this. Missing the current version
        would show a healthy document as 'queued' until something refetched it."""
        document = await add_document(uow_factory, owner, "doc")
        updated = await UpdateDocument(uow_factory).execute(
            owner, document.id, edit=DocumentEdit(title="New"), expected_version=document.version
        )
        assert updated.current_version is not None
        assert updated.current_version.status is ProcessingStatus.PENDING

    async def test_title_and_folder_can_change_together(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Reports", parent_id=None)
        document = await add_document(uow_factory, owner, "doc")

        updated = await UpdateDocument(uow_factory).execute(
            owner,
            document.id,
            edit=DocumentEdit(title="Final", move_to_folder=True, folder_id=folder.id),
            expected_version=document.version,
        )

        assert (updated.title, updated.folder_id) == ("Final", folder.id)
        assert updated.version == document.version + 1

    async def test_a_title_only_edit_leaves_the_folder_alone(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Reports", parent_id=None)
        document = await add_document(uow_factory, owner, "doc", folder_id=folder.id)

        updated = await UpdateDocument(uow_factory).execute(
            owner, document.id, edit=DocumentEdit(title="Renamed"), expected_version=1
        )

        assert updated.folder_id == folder.id

    async def test_moving_to_none_takes_the_document_out_of_its_folder(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Reports", parent_id=None)
        document = await add_document(uow_factory, owner, "doc", folder_id=folder.id)

        updated = await UpdateDocument(uow_factory).execute(
            owner,
            document.id,
            edit=DocumentEdit(move_to_folder=True, folder_id=None),
            expected_version=document.version,
        )

        assert updated.folder_id is None
        assert updated.title == "doc"

    async def test_filing_into_a_deleted_folder_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="Gone", parent_id=None)
        await DeleteFolder(uow_factory).execute(owner, folder.id)
        document = await add_document(uow_factory, owner, "doc")

        with pytest.raises(NotFoundError, match="no longer exists"):
            await UpdateDocument(uow_factory).execute(
                owner,
                document.id,
                edit=DocumentEdit(move_to_folder=True, folder_id=folder.id),
                expected_version=document.version,
            )

    async def test_filing_into_another_workspaces_folder_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        theirs = await CreateFolder(uow_factory).execute(stranger, name="Theirs", parent_id=None)
        document = await add_document(uow_factory, owner, "doc")
        with pytest.raises(NotFoundError):
            await UpdateDocument(uow_factory).execute(
                owner,
                document.id,
                edit=DocumentEdit(move_to_folder=True, folder_id=theirs.id),
                expected_version=document.version,
            )

    async def test_a_stale_version_is_a_conflict(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        update = UpdateDocument(uow_factory)
        await update.execute(
            owner, document.id, edit=DocumentEdit(title="First"), expected_version=1
        )
        with pytest.raises(ConflictError):
            await update.execute(
                owner, document.id, edit=DocumentEdit(title="Second"), expected_version=1
            )

    async def test_a_viewer_cannot_edit(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "doc")
        with pytest.raises(PermissionDeniedError):
            await UpdateDocument(uow_factory).execute(
                viewer, document.id, edit=DocumentEdit(title="x"), expected_version=1
            )

    async def test_a_refused_move_leaves_the_title_change_undone_too(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """One atomic change: a rename that rides along with a bad move must not stick."""
        document = await add_document(uow_factory, owner, "Original")
        with pytest.raises(NotFoundError):
            await UpdateDocument(uow_factory).execute(
                owner,
                document.id,
                edit=DocumentEdit(title="Renamed", move_to_folder=True, folder_id=uuid.uuid4()),
                expected_version=document.version,
            )
        unchanged = await uow_factory().documents.get(owner, document.id)
        assert unchanged is not None
        assert (unchanged.title, unchanged.version) == ("Original", document.version)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def _titles(
    uow_factory: FakeUnitOfWorkFactory,
    ctx: AccessContext,
    query: DocumentListQuery,
    *,
    limit: int = 50,
) -> list[str]:
    page = await ListDocuments(uow_factory).execute(ctx, limit=limit, query=query)
    return [d.title for d in page.items]


class TestListingFilters:
    async def test_by_folder_and_unfiled(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="F", parent_id=None)
        await add_document(uow_factory, owner, "filed", folder_id=folder.id)
        await add_document(uow_factory, owner, "loose")

        assert await _titles(uow_factory, owner, DocumentListQuery(folder_id=folder.id)) == [
            "filed"
        ]
        assert await _titles(uow_factory, owner, DocumentListQuery(unfiled=True)) == ["loose"]

    async def test_by_status(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        ready = await add_document(uow_factory, owner, "ready")
        await add_document(uow_factory, owner, "pending")
        assert ready.current_version is not None
        await uow_factory().documents.set_version_status(
            owner, ready.current_version.id, outcome=ProcessingOutcome.ready(chunk_count=1)
        )
        assert await _titles(
            uow_factory, owner, DocumentListQuery(status=ProcessingStatus.READY)
        ) == ["ready"]

    async def test_a_document_must_carry_every_selected_tag(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        create, add = CreateTag(uow_factory), AddDocumentTag(uow_factory)
        red = await create.execute(owner, name="red", color=None)
        blue = await create.execute(owner, name="blue", color=None)
        both = await add_document(uow_factory, owner, "both")
        only_red = await add_document(uow_factory, owner, "only red")
        for tag in (red, blue):
            await add.execute(owner, both.id, tag.id)
        await add.execute(owner, only_red.id, red.id)

        assert sorted(await _titles(uow_factory, owner, DocumentListQuery(tag_ids=(red.id,)))) == [
            "both",
            "only red",
        ]
        assert await _titles(uow_factory, owner, DocumentListQuery(tag_ids=(red.id, blue.id))) == [
            "both"
        ]

    async def test_title_text_matches_a_substring_ignoring_case(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        await add_document(uow_factory, owner, "Quarterly Budget Review")
        await add_document(uow_factory, owner, "Meeting notes")
        assert await _titles(uow_factory, owner, DocumentListQuery(text="  BUDGET ")) == [
            "Quarterly Budget Review"
        ]

    async def test_filters_compose(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        folder = await CreateFolder(uow_factory).execute(owner, name="F", parent_id=None)
        await add_document(uow_factory, owner, "budget in folder", folder_id=folder.id)
        await add_document(uow_factory, owner, "budget loose")
        await add_document(uow_factory, owner, "other in folder", folder_id=folder.id)

        assert await _titles(
            uow_factory, owner, DocumentListQuery(folder_id=folder.id, text="budget")
        ) == ["budget in folder"]


class TestListingOrderAndPaging:
    async def _seed(self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext) -> None:
        for title in ("delta", "Alpha", "charlie", "Bravo", "echo"):
            await add_document(uow_factory, owner, title)

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [
            (DocumentSort.TITLE_ASC, ["Alpha", "Bravo", "charlie", "delta", "echo"]),
            (DocumentSort.TITLE_DESC, ["echo", "delta", "charlie", "Bravo", "Alpha"]),
            (DocumentSort.CREATED_ASC, ["delta", "Alpha", "charlie", "Bravo", "echo"]),
            (DocumentSort.CREATED_DESC, ["echo", "Bravo", "charlie", "Alpha", "delta"]),
        ],
    )
    async def test_every_sort_orders_as_named(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        owner: AccessContext,
        sort: DocumentSort,
        expected: list[str],
    ) -> None:
        await self._seed(uow_factory, owner)
        assert await _titles(uow_factory, owner, DocumentListQuery(sort=sort)) == expected

    async def test_titles_sort_case_insensitively(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """'Bravo' belongs between 'Alpha' and 'charlie', not after every lowercase
        title as a byte-order sort would put it."""
        await self._seed(uow_factory, owner)
        titles = await _titles(uow_factory, owner, DocumentListQuery(sort=DocumentSort.TITLE_ASC))
        assert titles.index("Bravo") < titles.index("charlie")

    @pytest.mark.parametrize("sort", list(DocumentSort))
    async def test_paging_through_every_sort_visits_each_document_exactly_once(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, sort: DocumentSort
    ) -> None:
        await self._seed(uow_factory, owner)
        list_docs = ListDocuments(uow_factory)
        query = DocumentListQuery(sort=sort)

        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = await list_docs.execute(owner, limit=2, cursor=cursor, query=query)
            seen.extend(d.title for d in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break

        full = await _titles(uow_factory, owner, query)
        assert seen == full
        assert len(set(seen)) == 5

    async def test_a_cursor_is_refused_under_a_different_sort(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        await self._seed(uow_factory, owner)
        first = await ListDocuments(uow_factory).execute(
            owner, limit=2, query=DocumentListQuery(sort=DocumentSort.TITLE_ASC)
        )
        assert first.next_cursor is not None
        with pytest.raises(BadRequestError, match="not valid"):
            await ListDocuments(uow_factory).execute(
                owner,
                limit=2,
                cursor=first.next_cursor,
                query=DocumentListQuery(sort=DocumentSort.CREATED_DESC),
            )

    async def test_recently_updated_reflects_an_edit(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        old = await add_document(uow_factory, owner, "old")
        await add_document(uow_factory, owner, "newer")
        await UpdateDocument(uow_factory).execute(
            owner, old.id, edit=DocumentEdit(title="old, edited"), expected_version=1
        )
        assert (
            await _titles(
                uow_factory, owner, DocumentListQuery(sort=DocumentSort.UPDATED_DESC), limit=1
            )
        )[0] == "old, edited"


# ---------------------------------------------------------------------------
# Versions, downloads, content
# ---------------------------------------------------------------------------


async def _with_versions(
    uow_factory: FakeUnitOfWorkFactory, ctx: AccessContext, count: int
) -> Document:
    document = await add_document(uow_factory, ctx, "history")
    for index in range(1, count):
        await uow_factory().documents.add_version(ctx, document.id, content=content(f"v{index}"))
    reloaded = await uow_factory().documents.get(ctx, document.id)
    assert reloaded is not None
    return reloaded


class TestVersionHistory:
    async def test_newest_first_with_exactly_one_current(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await _with_versions(uow_factory, owner, 3)
        page = await ListDocumentVersions(uow_factory).execute(owner, document.id)

        assert [v.version_number for v in page.items] == [3, 2, 1]
        assert [v.is_current for v in page.items] == [True, False, False]
        assert page.next_before is None

    async def test_pages_backwards_with_before(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await _with_versions(uow_factory, owner, 5)
        lister = ListDocumentVersions(uow_factory)

        first = await lister.execute(owner, document.id, limit=2)
        assert [v.version_number for v in first.items] == [5, 4]
        assert first.next_before == 4

        second = await lister.execute(owner, document.id, before=first.next_before, limit=2)
        assert [v.version_number for v in second.items] == [3, 2]

        last = await lister.execute(owner, document.id, before=second.next_before, limit=2)
        assert [v.version_number for v in last.items] == [1]
        assert last.next_before is None

    async def test_a_missing_document_is_not_found_not_an_empty_history(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        with pytest.raises(NotFoundError):
            await ListDocumentVersions(uow_factory).execute(owner, uuid.uuid4())

    async def test_another_workspaces_document_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        document = await _with_versions(uow_factory, owner, 2)
        with pytest.raises(NotFoundError):
            await ListDocumentVersions(uow_factory).execute(stranger, document.id)

    async def test_a_viewer_can_read_the_history(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        document = await _with_versions(uow_factory, owner, 2)
        page = await ListDocumentVersions(uow_factory).execute(viewer, document.id)
        assert len(page.items) == 2


class TestVersionDownload:
    async def test_an_earlier_version_can_be_downloaded(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        storage = FakeObjectStorage()
        document = await _with_versions(uow_factory, owner, 2)
        old = (await uow_factory().documents.list_versions(owner, document.id))[-1]
        await _store(storage, old.storage_key)

        link = await GetDocumentDownload(uow_factory, storage).execute(
            owner, document.id, version_id=old.id
        )

        assert old.storage_key in link.url

    async def test_without_a_version_it_is_the_current_one(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        storage = FakeObjectStorage()
        document = await _with_versions(uow_factory, owner, 2)
        assert document.current_version is not None
        await _store(storage, document.current_version.storage_key)

        link = await GetDocumentDownload(uow_factory, storage).execute(owner, document.id)

        assert document.current_version.storage_key in link.url

    async def test_another_documents_version_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """A version id from a different document must not become a way to fetch
        that document's file by naming this one."""
        storage = FakeObjectStorage()
        mine = await add_document(uow_factory, owner, "mine")
        theirs = await add_document(uow_factory, owner, "theirs")
        assert theirs.current_version is not None
        await _store(storage, theirs.current_version.storage_key)

        with pytest.raises(NotFoundError):
            await GetDocumentDownload(uow_factory, storage).execute(
                owner, mine.id, version_id=theirs.current_version.id
            )

    async def test_a_version_of_another_workspace_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        storage = FakeObjectStorage()
        document = await add_document(uow_factory, owner, "doc")
        assert document.current_version is not None
        await _store(storage, document.current_version.storage_key)

        with pytest.raises(NotFoundError):
            await GetDocumentDownload(uow_factory, storage).execute(
                stranger, document.id, version_id=document.current_version.id
            )

    async def test_an_archived_document_can_still_be_downloaded(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        storage = FakeObjectStorage()
        document = await add_document(uow_factory, owner, "doc")
        assert document.current_version is not None
        await _store(storage, document.current_version.storage_key)
        await ArchiveDocument(uow_factory).execute(owner, document.id, archived=True)

        link = await GetDocumentDownload(uow_factory, storage).execute(viewer, document.id)
        assert link.url


async def _store(storage: FakeObjectStorage, key: str) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"%PDF-"

    await storage.put_stream(key, body(), content_type="application/pdf")


def _chunk(document: Document, ordinal: int, start: int, end: int, text: str) -> StoredChunk:
    assert document.current_version is not None
    return StoredChunk(
        version_id=document.current_version.id,
        document_id=document.id,
        workspace_id=document.workspace_id,
        ordinal=ordinal,
        text=text,
        char_start=start,
        char_end=end,
        page_start=1,
        page_end=1,
        heading_path=None,
        token_count=len(text.split()),
        embedding_model="fake",
        chunker_version="test",
        embedding=[0.0],
        embedding_dimensions=1,
        embedding_input_sha256="a" * 64,
        embedded_at=datetime.now(UTC) - timedelta(seconds=1),
    )


class TestReadingContent:
    TEXT = "AAAA BBBB. CCCC DDDD. EEEE FFFF. GGGG HHHH."

    async def _document_with_overlapping_chunks(
        self, uow_factory: FakeUnitOfWorkFactory, ctx: AccessContext
    ) -> Document:
        document = await add_document(uow_factory, ctx, "read me")
        assert document.current_version is not None
        # Each chunk repeats the last sentence of the one before it.
        uow_factory.state.chunks[document.current_version.id] = [
            _chunk(document, 0, 0, 21, self.TEXT[0:21]),  # AAAA BBBB. CCCC DDDD.
            _chunk(document, 1, 11, 32, self.TEXT[11:32]),  # CCCC DDDD. EEEE FFFF.
            _chunk(document, 2, 22, 43, self.TEXT[22:43]),  # EEEE FFFF. GGGG HHHH.
        ]
        return document

    async def test_neighbouring_passages_are_returned_without_repeated_text(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await self._document_with_overlapping_chunks(uow_factory, owner)
        result = await ReadDocumentContent(uow_factory).execute(owner, document.id)
        assert " ".join(p.text for p in result.items) == self.TEXT

    async def test_paging_trims_the_seam_between_pages_too(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await self._document_with_overlapping_chunks(uow_factory, owner)
        reader = ReadDocumentContent(uow_factory)

        first = await reader.execute(owner, document.id, limit=1)
        assert first.next_after == 0
        second = await reader.execute(owner, document.id, after=first.next_after, limit=1)
        third = await reader.execute(owner, document.id, after=second.next_after, limit=1)

        pieces = [*first.items, *second.items, *third.items]
        assert " ".join(p.text for p in pieces) == self.TEXT
        assert third.next_after is None

    async def test_a_document_that_is_not_processed_has_no_passages_but_still_answers(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        document = await add_document(uow_factory, owner, "fresh")
        result = await ReadDocumentContent(uow_factory).execute(owner, document.id)
        assert result.items == ()
        assert result.version.status is ProcessingStatus.PENDING

    async def test_a_new_version_has_no_text_until_it_is_processed(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext
    ) -> None:
        """A superseded version's passages are deleted, so the reader never shows text
        the document no longer contains."""
        document = await self._document_with_overlapping_chunks(uow_factory, owner)
        await uow_factory().documents.add_version(owner, document.id, content=content("rev2"))

        result = await ReadDocumentContent(uow_factory).execute(owner, document.id)

        assert result.items == ()
        assert result.version.version_number == 2

    async def test_every_role_can_read(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, viewer: AccessContext
    ) -> None:
        document = await self._document_with_overlapping_chunks(uow_factory, owner)
        result = await ReadDocumentContent(uow_factory).execute(viewer, document.id)
        assert len(result.items) == 3

    async def test_another_workspaces_document_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, owner: AccessContext, stranger: AccessContext
    ) -> None:
        document = await self._document_with_overlapping_chunks(uow_factory, owner)
        with pytest.raises(NotFoundError):
            await ReadDocumentContent(uow_factory).execute(stranger, document.id)
