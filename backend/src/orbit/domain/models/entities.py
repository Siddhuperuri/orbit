"""Domain entities.

Plain, framework-free objects. They carry no SQLAlchemy state, so business rules
can be unit-tested with no database, and a lazily-unloaded relationship cannot
escape into the application or HTTP layers.

Repositories accept and return these on the **write** path. The read path
returns purpose-built read models instead (see `read_models.py`), because
reconstructing a full aggregate to render a list is wasted work and invites
N+1 queries (ADR-0010).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from orbit.domain.access import Role

#: A SHA-256 digest rendered as lowercase hex.
SHA256_HEX_LENGTH = 64


class ProcessingStatus(StrEnum):
    """Mirrors the database enum. Declared here so the domain does not import
    infrastructure to name its own states."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (ProcessingStatus.READY, ProcessingStatus.FAILED)


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    token_epoch: int
    created_at: datetime
    email_verified_at: datetime | None = None
    last_login_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def can_authenticate(self) -> bool:
        """A suspended or deleted account must not be able to obtain a session."""
        return self.is_active and not self.is_deleted


@dataclass(frozen=True, slots=True)
class Workspace:
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    version: int
    created_by_user_id: uuid.UUID | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True, slots=True)
class Membership:
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    created_at: datetime
    invited_by_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """One revision of a document's content. Immutable except for its status."""

    id: uuid.UUID
    document_id: uuid.UUID
    workspace_id: uuid.UUID
    version_number: int
    is_current: bool
    storage_key: str
    content_sha256: str
    byte_size: int
    content_type: str
    original_filename: str
    status: ProcessingStatus
    chunk_count: int
    created_at: datetime
    failure_code: str | None = None
    failure_reason: str | None = None
    page_count: int | None = None
    language: str | None = None
    processed_at: datetime | None = None
    created_by_user_id: uuid.UUID | None = None
    #: The pipeline stage the version's latest attempt is in (a `PipelineStage`
    #: value), and only while it is still `PENDING` or `PROCESSING`. `None` once
    #: the version is `READY` or `FAILED`, and for a version no worker has claimed.
    #: Read from the job row, so it is what the worker last reported -- not a guess
    #: derived from elapsed time.
    processing_stage: str | None = None

    @property
    def is_searchable(self) -> bool:
        """Whether this version's content can answer a question.

        `READY` already implies `chunk_count > 0` -- a database constraint
        enforces it -- so this is a readable restatement rather than a second
        rule that could drift.
        """
        return self.status is ProcessingStatus.READY and self.chunk_count > 0


@dataclass(frozen=True, slots=True)
class TagRef:
    """A tag as it appears on a document: just enough to draw its chip."""

    id: uuid.UUID
    name: str
    color: str


@dataclass(frozen=True, slots=True)
class Document:
    """The identity of a document: what it is called and where it lives.

    Content facts belong to its versions, so renaming or refiling a document
    never touches its bytes, and replacing its bytes never disturbs how it has
    been organised.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    version: int
    folder_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None
    deleted_at: datetime | None = None
    #: Set while the document is archived: out of the working set and out of
    #: retrieval, but intact and restorable. Distinct from `deleted_at`, which
    #: is what makes a document unreachable.
    archived_at: datetime | None = None
    #: Populated only when the caller asked for it; `None` means "not loaded",
    #: which is distinct from "this document has no current version".
    current_version: DocumentVersion | None = None
    #: Ordered by name. Empty for a document with no tags *and* for a caller
    #: that did not load them -- repositories always load them, so a document
    #: that reaches the API has the real list.
    tags: tuple[TagRef, ...] = ()

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def renamed(self, title: str) -> Document:
        """Return a copy with a new title.

        Entities are frozen, so a change produces a new value rather than
        mutating shared state. The repository is what decides whether that value
        reaches the database.
        """
        cleaned = title.strip()
        if not cleaned:
            msg = "A document title cannot be blank."
            raise ValueError(msg)
        return replace(self, title=cleaned)


@dataclass(frozen=True, slots=True)
class VersionPage:
    """One page of a document's revisions, newest first.

    Paged by version number (`before`) rather than by a signed cursor: the number
    is already a total order that new uploads only ever extend at the top, so a
    reader paging backwards through history is never disturbed by one.
    """

    items: tuple[DocumentVersion, ...]
    #: Pass as `before` for the next (older) page; `None` on the last.
    next_before: int | None


@dataclass(frozen=True, slots=True)
class Folder:
    """A node in a workspace's folder tree."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    parent_folder_id: uuid.UUID | None
    depth: int
    version: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class FolderListing:
    """A folder with what is in it, for drawing the tree and for deciding
    whether it may be deleted."""

    folder: Folder
    #: Live documents directly inside, not archived.
    document_count: int
    #: Live documents directly inside that are archived. Counted apart because
    #: they are out of the folder's default view but still pin the folder: a
    #: folder holding one cannot be deleted.
    archived_document_count: int
    #: Live subfolders directly inside.
    child_count: int

    @property
    def is_empty(self) -> bool:
        return self.document_count + self.archived_document_count + self.child_count == 0


@dataclass(frozen=True, slots=True)
class Tag:
    """A workspace-scoped label."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    color: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TagListing:
    tag: Tag
    #: Live documents carrying it, archived ones included: deleting the tag
    #: removes it from those too, so the number is the blast radius.
    document_count: int


@dataclass(frozen=True, slots=True)
class VersionContent:
    """The facts about one uploaded file.

    These five values are produced together by the upload pipeline -- the
    storage key it generated, the hash and size it computed while streaming, the
    content type it sniffed, and the filename the client sent -- and they are
    consumed together. Passing them as one value keeps that cohesion visible and
    makes it impossible to supply four of the five.
    """

    storage_key: str
    content_sha256: str
    byte_size: int
    content_type: str
    original_filename: str

    def __post_init__(self) -> None:
        # These are invariants of the value, not of the row, so they are checked
        # where the value is built rather than only at the database.
        if self.byte_size <= 0:
            msg = "byte_size must be positive."
            raise ValueError(msg)
        if len(self.content_sha256) != SHA256_HEX_LENGTH:
            msg = "content_sha256 must be a 64-character hex digest."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NewDocumentIds:
    """Pre-generated ids for a document being created by the upload pipeline.

    Exists to keep `DocumentRepository.create` at five parameters rather than
    six: without this, `document_id` and `version_id` would each be their own
    keyword argument. The two travel together for one reason -- ADR-0011's
    storage key is built from both *before* either row exists
    (`core/storage_keys.py`), so a caller that has one has always generated
    the other in the same breath.
    """

    document_id: uuid.UUID
    version_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    """The result of one processing attempt.

    Bundled so that an inconsistent combination -- `FAILED` with no reason,
    `READY` with no chunks -- is awkward to construct rather than merely
    rejected later by a check constraint.
    """

    status: ProcessingStatus
    chunk_count: int | None = None
    page_count: int | None = None
    failure_code: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is ProcessingStatus.FAILED and not self.failure_code:
            msg = "A failed outcome must carry a failure_code."
            raise ValueError(msg)
        if self.status is ProcessingStatus.READY and not self.chunk_count:
            # Mirrors ck_document_versions_ready_versions_have_chunks: a READY
            # version with no chunks answers no questions while looking healthy.
            msg = "A ready outcome must have at least one chunk."
            raise ValueError(msg)

    @classmethod
    def ready(cls, *, chunk_count: int, page_count: int | None = None) -> ProcessingOutcome:
        return cls(status=ProcessingStatus.READY, chunk_count=chunk_count, page_count=page_count)

    @classmethod
    def failed(cls, *, code: str, reason: str) -> ProcessingOutcome:
        return cls(status=ProcessingStatus.FAILED, failure_code=code, failure_reason=reason)


@dataclass(frozen=True, slots=True)
class RefreshTokenSession:
    """One issued refresh token, as the application reasons about it.

    Never carries the plaintext token -- only its hash, which is all the
    repository layer ever sees (core/tokens.py hashes it before this entity
    exists).
    """

    id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_usable(self) -> bool:
        """Whether this token may still be redeemed for a new pair.

        A token that is expired, already consumed, or belongs to a revoked
        family is unusable for exactly the same reason: presenting it now
        would not be the legitimate holder's normal next action.
        """
        return self.consumed_at is None and self.revoked_at is None


@dataclass(frozen=True, slots=True)
class NewRefreshToken:
    """The facts needed to issue one refresh token.

    Bundled for the same reason as `VersionContent`: these values are produced
    together by the login/refresh use case and consumed together by the
    repository, so passing them separately would let a caller supply four of
    the six and invite the fifth argument to silently drift out of order.
    """

    user_id: uuid.UUID
    family_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    client_ip: str | None = None
    user_agent: str | None = None


class AccountTokenPurpose(StrEnum):
    """What a one-time account token authorises.

    Password reset and email verification share one table and one entity
    because they are the same mechanism -- a hashed, single-use, expiring
    secret mailed to an address -- differing only in what redeeming it does.
    Two tables would duplicate the issue/consume/expire logic and, with it,
    the opportunity to get that logic subtly different in one of them.

    The purpose is carried *in the row*, and redemption checks it, so a
    verification token can never be redeemed as a password reset.
    """

    # S105 reads the member name as a credential; it names a purpose.
    PASSWORD_RESET = "password_reset"  # noqa: S105
    EMAIL_VERIFICATION = "email_verification"


@dataclass(frozen=True, slots=True)
class AccountToken:
    """A one-time secret sent to a user's email address.

    Never carries the plaintext token -- only its SHA-256 hash, which is all
    the repository layer ever sees. A database leak therefore yields nothing
    redeemable, exactly as for refresh tokens.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    purpose: AccountTokenPurpose
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None

    def is_redeemable_at(self, now: datetime) -> bool:
        """Whether this token may still be spent.

        Single-use and time-boxed. Both conditions are checked here, and the
        caller must not reimplement either -- a reset token that stays valid
        after use is an account takeover waiting for a leaked inbox.
        """
        return self.consumed_at is None and self.expires_at > now
