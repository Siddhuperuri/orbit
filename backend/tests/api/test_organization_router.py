"""Folders, tags, archive, versions, content, and the new document filters: the HTTP contract.

As in `test_documents_router.py`, every route carries `{workspace_id}`, so `as_role`
must be called in every test. The use cases are stubbed: what is under test here is
request validation, how the body and query become the arguments a use case sees, and
how domain errors become responses.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api import deps
from orbit.application.documents.get_document_download import DownloadLink
from orbit.application.documents.read_document_content import DocumentContent
from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import (
    ArchiveFilter,
    DocumentEdit,
    DocumentListQuery,
    DocumentSort,
    Passage,
)
from orbit.domain.errors import (
    FolderNotEmptyError,
    NotFoundError,
    PermissionDeniedError,
)
from orbit.domain.models.entities import (
    Document,
    DocumentVersion,
    Folder,
    FolderListing,
    ProcessingStatus,
    Tag,
    TagListing,
    TagRef,
    User,
    VersionPage,
)
from orbit.domain.models.pagination import Page

OverrideFn = Callable[..., None]
#: What httpx accepts for a repeated query key, spelled out because list is invariant.
RepeatedParams = list[tuple[str, str | int | float | bool | None]]

WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"


def _user() -> User:
    return User(
        id=USER_ID,
        email="ada@example.com",
        full_name="Ada",
        is_active=True,
        token_epoch=0,
        created_at=NOW,
    )


def _version(number: int = 1, *, current: bool = True) -> DocumentVersion:
    return DocumentVersion(
        id=uuid.uuid4(),
        document_id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        version_number=number,
        is_current=current,
        storage_key="secret/storage/key",
        content_sha256="ab" * 32,
        byte_size=2048,
        content_type="application/pdf",
        original_filename="report.pdf",
        status=ProcessingStatus.READY,
        chunk_count=12,
        created_at=NOW,
        created_by_user_id=USER_ID,
    )


def _document(**overrides: object) -> Document:
    fields: dict[str, object] = {
        "id": DOCUMENT_ID,
        "workspace_id": WORKSPACE_ID,
        "title": "Quarterly Report",
        "created_at": NOW,
        "updated_at": NOW,
        "version": 3,
        "created_by_user_id": USER_ID,
        "current_version": _version(),
    }
    fields.update(overrides)
    return Document(**fields)  # type: ignore[arg-type]


def _folder(name: str = "Reports", parent: uuid.UUID | None = None) -> Folder:
    return Folder(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE_ID,
        name=name,
        parent_folder_id=parent,
        depth=0 if parent is None else 1,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _tag(name: str = "Urgent", color: str = "danger") -> Tag:
    return Tag(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE_ID,
        name=name,
        color=color,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


class _Stub:
    """A use case that returns (or raises) a fixed result and records its calls."""

    def __init__(self, result: object = None) -> None:
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __deepcopy__(self, memo: dict[int, object]) -> _Stub:
        # FastAPI sees `lambda stub=stub: stub` as a parameter *with a default*, and
        # Pydantic deep-copies defaults -- so the endpoint would call a copy, and the
        # calls recorded here would never show up on the instance the test holds.
        return self

    async def execute(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args[1:], kwargs))  # drop the AccessContext
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    @property
    def kwargs(self) -> dict[str, object]:
        return self.calls[-1][1]

    @property
    def args(self) -> tuple[object, ...]:
        return self.calls[-1][0]


_PROVIDERS = {
    "list_documents": deps.get_list_documents,
    "update_document": deps.get_update_document,
    "archive_document": deps.get_archive_document,
    "list_versions": deps.get_list_document_versions,
    "read_content": deps.get_read_document_content,
    "add_tag": deps.get_add_document_tag,
    "remove_tag": deps.get_remove_document_tag,
    "download": deps.get_get_document_download,
    "list_folders": deps.get_list_folders,
    "create_folder": deps.get_create_folder,
    "rename_folder": deps.get_rename_folder,
    "delete_folder": deps.get_delete_folder,
    "list_tags": deps.get_list_tags,
    "create_tag": deps.get_create_tag,
    "update_tag": deps.get_update_tag,
    "delete_tag": deps.get_delete_tag,
}


@pytest.fixture
def override(app: FastAPI) -> Iterator[OverrideFn]:
    def _apply(**stubs: _Stub) -> None:
        for name, stub in stubs.items():
            app.dependency_overrides[_PROVIDERS[name]] = lambda stub=stub: stub

    app.dependency_overrides[deps.get_current_user] = _user
    yield _apply
    app.dependency_overrides.clear()


@pytest.fixture
def as_role(app: FastAPI) -> Callable[[Role], None]:
    def _apply(role: Role) -> None:
        async def _context() -> AccessContext:
            return AccessContext(user_id=USER_ID, workspace_id=WORKSPACE_ID, role=role)

        app.dependency_overrides[deps.get_access_context] = _context

    return _apply


def _denied() -> PermissionDeniedError:
    return PermissionDeniedError("You do not have permission to perform this action.")


# ---------------------------------------------------------------------------
# PATCH /documents/{id}: presence, not value
# ---------------------------------------------------------------------------


class TestUpdateDocumentBody:
    """`folder_id` means three different things by presence, and getting one wrong
    silently misfiles a document."""

    URL = f"{BASE}/documents/{DOCUMENT_ID}"

    def _send(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        body: dict[str, object],
    ) -> tuple[_Stub, int]:
        as_role(Role.MEMBER)
        stub = _Stub(_document())
        override(update_document=stub)
        return stub, client.patch(self.URL, json=body).status_code

    def test_a_title_alone_does_not_touch_the_folder(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, status = self._send(
            client, override, as_role, {"title": "New", "expected_version": 3}
        )
        assert status == 200
        edit = stub.kwargs["edit"]
        assert isinstance(edit, DocumentEdit)
        assert (edit.title, edit.move_to_folder, edit.folder_id) == ("New", False, None)

    def test_an_explicit_null_takes_the_document_out_of_its_folder(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, status = self._send(
            client, override, as_role, {"folder_id": None, "expected_version": 3}
        )
        assert status == 200
        edit = stub.kwargs["edit"]
        assert isinstance(edit, DocumentEdit)
        assert (edit.title, edit.move_to_folder, edit.folder_id) == (None, True, None)

    def test_an_id_files_it_there(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        target = uuid.uuid4()
        stub, status = self._send(
            client, override, as_role, {"folder_id": str(target), "expected_version": 3}
        )
        assert status == 200
        edit = stub.kwargs["edit"]
        assert isinstance(edit, DocumentEdit)
        assert (edit.move_to_folder, edit.folder_id) == (True, target)

    def test_the_expected_version_is_passed_through(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, _ = self._send(client, override, as_role, {"title": "x", "expected_version": 7})
        assert stub.kwargs["expected_version"] == 7

    def test_an_empty_edit_is_a_400_not_a_silent_no_op(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, status = self._send(client, override, as_role, {"expected_version": 3})
        assert status == 400
        assert stub.calls == []

    def test_the_expected_version_is_required(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, status = self._send(client, override, as_role, {"title": "x"})
        assert status == 422
        assert stub.calls == []

    def test_unknown_fields_are_refused_rather_than_ignored(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        stub, status = self._send(
            client, override, as_role, {"title": "x", "expected_version": 1, "archived_at": None}
        )
        assert status == 422
        assert stub.calls == []

    def test_a_blank_title_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        _, status = self._send(client, override, as_role, {"title": "  ", "expected_version": 1})
        assert status == 422

    def test_the_response_carries_the_whole_document(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        tagged = _document(tags=(TagRef(id=uuid.uuid4(), name="Urgent", color="danger"),))
        override(update_document=_Stub(tagged))

        body = client.patch(self.URL, json={"title": "x", "expected_version": 3}).json()

        assert body["current_version"]["status"] == "ready"
        assert body["tags"] == [{"id": str(tagged.tags[0].id), "name": "Urgent", "color": "danger"}]
        assert body["archived_at"] is None
        assert body["version"] == 3

    def test_the_storage_key_is_never_exposed(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(update_document=_Stub(_document()))
        text = client.patch(self.URL, json={"title": "x", "expected_version": 3}).text
        assert "secret/storage/key" not in text

    def test_a_viewer_is_refused(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(update_document=_Stub(_denied()))
        response = client.patch(self.URL, json={"title": "x", "expected_version": 1})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# List query parameters
# ---------------------------------------------------------------------------


class TestListDocumentsQuery:
    URL = f"{BASE}/documents"

    def _query(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        params: dict[str, str] | RepeatedParams,
    ) -> DocumentListQuery:
        as_role(Role.VIEWER)
        stub = _Stub(Page(items=(), next_cursor=None))
        override(list_documents=stub)
        response = client.get(self.URL, params=params)
        assert response.status_code == 200, response.text
        query = stub.kwargs["query"]
        assert isinstance(query, DocumentListQuery)
        return query

    def test_the_defaults_are_the_working_set_newest_first(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        query = self._query(client, override, as_role, {})
        assert query == DocumentListQuery()

    def test_every_filter_is_parsed(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        folder, tag_a, tag_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        query = self._query(
            client,
            override,
            as_role,
            [
                ("folder_id", str(folder)),
                ("status", "ready"),
                ("tag_id", str(tag_a)),
                ("tag_id", str(tag_b)),
                ("q", "budget"),
                ("sort", "title_asc"),
                ("archive", "archived"),
            ],
        )
        assert query.folder_id == folder
        assert query.status is ProcessingStatus.READY
        assert set(query.tag_ids) == {tag_a, tag_b}
        assert query.text == "budget"
        assert query.sort is DocumentSort.TITLE_ASC
        assert query.archive is ArchiveFilter.ARCHIVED

    def test_unfiled_is_a_flag(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        assert self._query(client, override, as_role, {"unfiled": "true"}).unfiled is True

    def test_a_folder_and_unfiled_together_are_a_400(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        stub = _Stub(Page(items=(), next_cursor=None))
        override(list_documents=stub)
        response = client.get(self.URL, params={"folder_id": str(uuid.uuid4()), "unfiled": "true"})
        assert response.status_code == 400
        assert stub.calls == []

    @pytest.mark.parametrize(
        "params",
        [
            {"sort": "sideways"},
            {"archive": "everything"},
            {"tag_id": "not-a-uuid"},
            {"q": "x" * 101},
            {"folder_id": "nope"},
        ],
    )
    def test_invalid_values_are_a_422(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        params: dict[str, str],
    ) -> None:
        as_role(Role.VIEWER)
        stub = _Stub(Page(items=(), next_cursor=None))
        override(list_documents=stub)
        assert client.get(self.URL, params=params).status_code == 422
        assert stub.calls == []

    def test_more_than_five_tags_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_documents=_Stub(Page(items=(), next_cursor=None)))
        params: RepeatedParams = [("tag_id", str(uuid.uuid4())) for _ in range(6)]
        assert client.get(self.URL, params=params).status_code == 422


# ---------------------------------------------------------------------------
# Archive, tag attach/detach
# ---------------------------------------------------------------------------


class TestArchiveAndTags:
    def test_archive_and_restore_ask_for_opposite_states(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_document(archived_at=NOW))
        override(archive_document=stub)

        archived = client.post(f"{BASE}/documents/{DOCUMENT_ID}/archive")
        assert archived.status_code == 200
        assert stub.kwargs["archived"] is True
        assert archived.json()["archived_at"] is not None

        client.post(f"{BASE}/documents/{DOCUMENT_ID}/restore")
        assert stub.kwargs["archived"] is False

    def test_a_viewer_cannot_archive(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(archive_document=_Stub(_denied()))
        assert client.post(f"{BASE}/documents/{DOCUMENT_ID}/archive").status_code == 403

    def test_tagging_puts_and_untagging_deletes(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        tag_id = uuid.uuid4()
        add, remove = _Stub(_document()), _Stub(_document())
        override(add_tag=add, remove_tag=remove)
        url = f"{BASE}/documents/{DOCUMENT_ID}/tags/{tag_id}"

        assert client.put(url).status_code == 200
        assert add.args == (DOCUMENT_ID, tag_id)
        assert client.delete(url).status_code == 200
        assert remove.args == (DOCUMENT_ID, tag_id)

    def test_a_missing_tag_is_a_404(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(add_tag=_Stub(NotFoundError("That tag no longer exists.")))
        response = client.put(f"{BASE}/documents/{DOCUMENT_ID}/tags/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Versions and content
# ---------------------------------------------------------------------------


class TestVersionsAndContent:
    def test_the_history_and_its_cursor(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        stub = _Stub(VersionPage(items=(_version(3), _version(2, current=False)), next_before=2))
        override(list_versions=stub)

        response = client.get(
            f"{BASE}/documents/{DOCUMENT_ID}/versions", params={"before": 5, "limit": 2}
        )

        assert response.status_code == 200
        body = response.json()
        assert [v["version_number"] for v in body["items"]] == [3, 2]
        assert body["next_before"] == 2
        assert stub.kwargs == {"before": 5, "limit": 2}
        assert "storage_key" not in response.text

    def test_the_history_reports_who_and_which_bytes(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_versions=_Stub(VersionPage(items=(_version(),), next_before=None)))
        item = client.get(f"{BASE}/documents/{DOCUMENT_ID}/versions").json()["items"][0]
        assert item["created_by_user_id"] == str(USER_ID)
        assert item["content_sha256"] == "ab" * 32

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"before": 0}])
    def test_history_bounds_are_enforced(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        params: dict[str, int],
    ) -> None:
        as_role(Role.VIEWER)
        override(list_versions=_Stub(VersionPage(items=(), next_before=None)))
        assert (
            client.get(f"{BASE}/documents/{DOCUMENT_ID}/versions", params=params).status_code == 422
        )

    def test_content_is_the_current_versions_passages(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        version = _version(2)
        passages = (
            Passage(
                ordinal=0,
                text="First.",
                char_start=0,
                char_end=6,
                page_from=1,
                page_to=1,
                heading_path="Intro",
            ),
            Passage(ordinal=1, text="Second.", char_start=7, char_end=14),
        )
        stub = _Stub(DocumentContent(version=version, items=passages, next_after=1))
        override(read_content=stub)

        response = client.get(
            f"{BASE}/documents/{DOCUMENT_ID}/content", params={"after": 0, "limit": 2}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["version_number"] == 2
        assert body["total_passages"] == 12
        assert body["next_after"] == 1
        assert body["items"][0] == {
            "ordinal": 0,
            "text": "First.",
            "heading_path": "Intro",
            "page_from": 1,
            "page_to": 1,
            "char_start": 0,
            "char_end": 6,
        }
        assert stub.kwargs == {"after": 0, "limit": 2}

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"after": -1}])
    def test_content_bounds_are_enforced(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        params: dict[str, int],
    ) -> None:
        as_role(Role.VIEWER)
        override(read_content=_Stub(None))
        assert (
            client.get(f"{BASE}/documents/{DOCUMENT_ID}/content", params=params).status_code == 422
        )

    def test_a_version_download_names_the_version(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        version_id = uuid.uuid4()
        stub = _Stub(DownloadLink(url="https://storage.test/x", expires_at=NOW))
        override(download=stub)

        response = client.get(f"{BASE}/documents/{DOCUMENT_ID}/versions/{version_id}/download")

        assert response.status_code == 200
        assert response.json()["url"] == "https://storage.test/x"
        assert stub.kwargs == {"version_id": version_id}


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


class TestFoldersApi:
    def test_the_list_carries_counts_and_order(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        parent = _folder("Parent")
        child = _folder("Child", parent.id)
        override(
            list_folders=_Stub(
                [
                    FolderListing(
                        parent, document_count=2, archived_document_count=1, child_count=1
                    ),
                    FolderListing(
                        child, document_count=0, archived_document_count=0, child_count=0
                    ),
                ]
            )
        )

        items = client.get(f"{BASE}/folders").json()["items"]

        assert [i["name"] for i in items] == ["Parent", "Child"]
        assert items[1]["parent_id"] == str(parent.id)
        assert (items[0]["document_count"], items[0]["archived_document_count"]) == (2, 1)
        assert items[0]["child_count"] == 1

    def test_create_returns_201(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        parent = uuid.uuid4()
        stub = _Stub(_folder("New", parent))
        override(create_folder=stub)

        response = client.post(f"{BASE}/folders", json={"name": "New", "parent_id": str(parent)})

        assert response.status_code == 201
        assert stub.kwargs == {"name": "New", "parent_id": parent}
        assert "document_count" not in response.json()

    def test_a_top_level_folder_needs_no_parent(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_folder())
        override(create_folder=stub)
        assert client.post(f"{BASE}/folders", json={"name": "Top"}).status_code == 201
        assert stub.kwargs["parent_id"] is None

    @pytest.mark.parametrize(
        "body", [{"name": ""}, {"name": "   "}, {"name": "x" * 256}, {}, {"name": "a", "x": 1}]
    )
    def test_bad_create_bodies_are_a_422(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        body: dict[str, object],
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_folder())
        override(create_folder=stub)
        assert client.post(f"{BASE}/folders", json=body).status_code == 422
        assert stub.calls == []

    def test_rename_needs_the_expected_version(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(rename_folder=_Stub(_folder()))
        url = f"{BASE}/folders/{uuid.uuid4()}"
        assert client.patch(url, json={"name": "x"}).status_code == 422
        assert client.patch(url, json={"name": "x", "expected_version": 1}).status_code == 200

    def test_deleting_a_full_folder_is_a_409_with_its_own_code(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(
            delete_folder=_Stub(
                FolderNotEmptyError("This folder isn't empty: it still holds 3 documents.")
            )
        )

        response = client.delete(f"{BASE}/folders/{uuid.uuid4()}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "FOLDER_NOT_EMPTY"
        assert "3 documents" in response.json()["error"]["message"]

    def test_delete_is_204(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(delete_folder=_Stub(None))
        assert client.delete(f"{BASE}/folders/{uuid.uuid4()}").status_code == 204

    def test_a_viewer_is_refused_every_write(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(
            create_folder=_Stub(_denied()),
            rename_folder=_Stub(_denied()),
            delete_folder=_Stub(_denied()),
        )
        folder = uuid.uuid4()
        assert client.post(f"{BASE}/folders", json={"name": "x"}).status_code == 403
        assert (
            client.patch(
                f"{BASE}/folders/{folder}", json={"name": "x", "expected_version": 1}
            ).status_code
            == 403
        )
        assert client.delete(f"{BASE}/folders/{folder}").status_code == 403


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTagsApi:
    def test_the_list_carries_usage(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_tags=_Stub([TagListing(_tag("Urgent"), 4), TagListing(_tag("Old"), 0)]))
        items = client.get(f"{BASE}/tags").json()["items"]
        assert [(i["name"], i["document_count"]) for i in items] == [("Urgent", 4), ("Old", 0)]

    def test_create_defaults_to_neutral(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_tag("Plain", "neutral"))
        override(create_tag=stub)
        response = client.post(f"{BASE}/tags", json={"name": "Plain"})
        assert response.status_code == 201
        assert stub.kwargs == {"name": "Plain", "color": "neutral"}

    @pytest.mark.parametrize("color", ["red", "#fff", "", "NEUTRAL", 3])
    def test_a_colour_outside_the_palette_is_a_422(
        self,
        client: TestClient,
        override: OverrideFn,
        as_role: Callable[[Role], None],
        color: object,
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_tag())
        override(create_tag=stub)
        assert client.post(f"{BASE}/tags", json={"name": "x", "color": color}).status_code == 422
        assert stub.calls == []

    def test_the_openapi_schema_publishes_the_palette_as_an_enum(self, client: TestClient) -> None:
        """The frontend types its colour picker from this, so it must be an enum and
        not a bare string."""
        schema = client.get("/openapi.json").json()["components"]["schemas"]["CreateTagRequest"]
        assert schema["properties"]["color"]["enum"] == [
            "neutral",
            "accent",
            "success",
            "warning",
            "danger",
        ]

    def test_update_takes_either_field_but_needs_the_version(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        stub = _Stub(_tag())
        override(update_tag=stub)
        url = f"{BASE}/tags/{uuid.uuid4()}"

        assert client.patch(url, json={"color": "accent"}).status_code == 422
        assert client.patch(url, json={"color": "accent", "expected_version": 2}).status_code == 200
        assert stub.kwargs == {"name": None, "color": "accent", "expected_version": 2}
        assert client.patch(url, json={"name": "Renamed", "expected_version": 2}).status_code == 200
        assert stub.kwargs["name"] == "Renamed"

    def test_delete_is_204_and_a_viewer_is_refused(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(delete_tag=_Stub(None))
        assert client.delete(f"{BASE}/tags/{uuid.uuid4()}").status_code == 204

        as_role(Role.VIEWER)
        override(delete_tag=_Stub(_denied()))
        assert client.delete(f"{BASE}/tags/{uuid.uuid4()}").status_code == 403


# ---------------------------------------------------------------------------
# The upload policy
# ---------------------------------------------------------------------------


class TestUploadPolicyOnMeta:
    def test_the_client_can_learn_the_real_limit(self, client: TestClient) -> None:
        body = client.get("/api/v1/meta").json()
        assert body["uploads"]["max_bytes"] == 52_428_800
        assert body["uploads"]["extensions"] == [".markdown", ".md", ".pdf", ".txt"]


class TestProcessingStageOnTheResponse:
    @staticmethod
    def _in_flight(stage: str | None) -> Document:
        version = dataclasses.replace(
            _version(),
            status=ProcessingStatus.PROCESSING,
            chunk_count=0,
            processing_stage=stage,
        )
        return _document(current_version=version)

    def test_a_reported_stage_round_trips(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(update_document=_Stub(self._in_flight("embed")))

        body = client.patch(
            f"{BASE}/documents/{DOCUMENT_ID}", json={"title": "x", "expected_version": 3}
        ).json()

        assert body["current_version"]["processing_stage"] == "embed"

    def test_no_stage_is_null_not_absent(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(update_document=_Stub(self._in_flight(None)))

        version = client.patch(
            f"{BASE}/documents/{DOCUMENT_ID}", json={"title": "x", "expected_version": 3}
        ).json()["current_version"]

        assert "processing_stage" in version
        assert version["processing_stage"] is None

    def test_the_openapi_schema_lists_every_stage(self, client: TestClient) -> None:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        assert schemas["PipelineStage"]["enum"] == [
            "claimed",
            "fetch",
            "parse",
            "normalize",
            "chunk",
            "embed",
            "index",
            "done",
        ]
