"""Documents, versions, folders, tags, and chunks.

Two decisions here shape everything downstream.

**Identity is separated from content.** A `documents` row is the *identity* of a
document -- its title, folder, tags, and the conversations that cite it. A
`document_versions` row is its *content* -- the stored bytes, their hash, and
the processing outcome for those bytes. Re-uploading a revised file therefore
preserves everything a user has organised around the document while replacing
what it says.

**Only the current version has chunks.** Chunks are derived, rebuildable, and
exist to be retrieved; keeping a superseded version's chunks would pollute
retrieval with text the document no longer contains, and would multiply the ANN
index for no benefit. Superseded versions keep their stored object -- so a
restore is possible -- but their chunks are hard-deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orbit.infrastructure.db.models.base import (
    Base,
    OptimisticVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# The width migration 0001 gave `chunks.embedding`. This constant describes the
# schema for autogenerate; it is not what the application trusts at runtime.
# The worker reads the real width from the catalog before embedding, and the
# readiness probe compares it with ORBIT_EMBEDDING_DIMENSIONS, so a mismatch is
# a refusal rather than a corruption (ADR-0020). Changing it is a data
# migration, not a configuration change: docs/database/embeddings.md.
EMBEDDING_DIMENSIONS = 1536

MAX_TITLE_LENGTH = 512
MAX_FILENAME_LENGTH = 255
MAX_FOLDER_DEPTH = 16


class ProcessingStatus(StrEnum):
    """Where a version is in the pipeline.

    Four states, all explicit. `FAILED` means the document is the problem;
    `PENDING` means we are -- a provider outage returns a version to `PENDING`
    rather than telling the user their file is broken
    (docs/architecture/data-flow.md).
    """

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


processing_status_enum = SAEnum(
    ProcessingStatus,
    name="processing_status",
    values_callable=lambda enum: [member.value for member in enum],
)


class FailureKind(StrEnum):
    """Why a job failed, which decides whether it is retried
    (domain/processing/failures.py)."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    DEFECT = "defect"


failure_kind_enum = SAEnum(
    FailureKind,
    name="failure_kind",
    values_callable=lambda enum: [member.value for member in enum],
)

job_status_enum = SAEnum(
    JobStatus, name="job_status", values_callable=lambda enum: [member.value for member in enum]
)


class Folder(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, OptimisticVersionMixin, Base):
    """A tree node for organising documents.

    Adjacency list rather than a materialised path: the queries that matter are
    "list children" and "breadcrumb", both cheap here, while a materialised path
    would make every move rewrite a subtree. Subtree queries use a recursive CTE.

    Depth is stored and bounded so that a pathological tree cannot make those
    recursive queries unbounded.
    """

    __tablename__ = "folders"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    parent_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        # Composite unique target so children can be constrained to their
        # parent's workspace by foreign key rather than by application check.
        UniqueConstraint("workspace_id", "id", name="uq_folders_workspace_id_id"),
        # NULLS NOT DISTINCT is essential: without it PostgreSQL treats every
        # NULL parent as unique, so any number of root folders could share a
        # name -- exactly the collision the constraint exists to prevent.
        Index(
            "uq_folders_parent_name",
            "workspace_id",
            "parent_folder_id",
            text("lower(name)"),
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_folders_workspace_id_parent_folder_id",
            "workspace_id",
            "parent_folder_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        # Prevents the trivial one-node cycle. Longer cycles are not expressible
        # as a table constraint and are rejected by the application, which walks
        # the ancestor chain before any move.
        CheckConstraint(
            "parent_folder_id IS NULL OR parent_folder_id <> id", name="no_self_parent"
        ),
        CheckConstraint(f"depth >= 0 AND depth <= {MAX_FOLDER_DEPTH}", name="depth_within_bounds"),
        CheckConstraint(
            "(parent_folder_id IS NULL) = (depth = 0)", name="root_folders_have_depth_zero"
        ),
    )


class Document(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, OptimisticVersionMixin, Base):
    """The identity of a document: what it is called and where it lives.

    Carries no content facts -- those belong to `document_versions`, so that
    replacing the file does not disturb the organisation built around it.
    """

    __tablename__ = "documents"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Archived is a visibility state, not a lifecycle one: the row, its versions,
    # its chunks and its stored object are all untouched, so restoring is
    # instant. Retrieval excludes archived documents (`repositories/search.py`);
    # everything else -- processing, download, the detail page -- still works.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", lazy="raise", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id_id"),
        # A composite foreign key, not a plain one. It makes filing a document
        # into another tenant's folder impossible at the storage layer rather
        # than merely rejected by a check somebody could forget to write.
        ForeignKeyConstraint(
            ["workspace_id", "folder_id"],
            ["folders.workspace_id", "folders.id"],
            name="fk_documents_folder_within_workspace",
            ondelete="SET NULL",
        ),
        # The product's most frequent query: keyset-paginated document list.
        # `id` is the tiebreaker, which is meaningful because UUIDv7 sorts
        # chronologically.
        Index(
            "ix_documents_workspace_id_created_at_id",
            "workspace_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_documents_workspace_id_folder_id",
            "workspace_id",
            "folder_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # One index per list ordering, so every sort pages by walking an index
        # rather than sorting the workspace on disk. Descending sorts read the
        # ascending title index backwards.
        Index(
            "ix_documents_workspace_id_updated_at_id",
            "workspace_id",
            text("updated_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_documents_workspace_id_lower_title_id",
            "workspace_id",
            text("lower(title)"),
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The archive view. Archived documents are few next to active ones, so a
        # partial index makes that view cheap without taxing every write to the
        # working set.
        Index(
            "ix_documents_workspace_id_created_at_id_archived",
            "workspace_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL AND archived_at IS NOT NULL"),
        ),
        # The title filter is a case-insensitive substring match, which a B-tree
        # cannot serve and a trigram index can. Needs `pg_trgm`, which
        # provisioning creates (`docker/postgres/initdb/00-extensions.sql`).
        Index(
            "ix_documents_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One uploaded revision of a document's content.

    Versions are immutable except for their processing status: the bytes, their
    hash, and their storage key never change once written. There is no soft
    delete -- a version dies with its document.
    """

    __tablename__ = "document_versions"

    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Exactly one current version per document, enforced by a partial unique
    # index below. Deriving "current" from a flag rather than from a pointer on
    # `documents` keeps one source of truth instead of two that can disagree.
    is_current: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # Entirely server-generated: workspaces/{ws}/documents/{doc}/{sha256}.
    # No user-supplied string reaches a path, so traversal is unrepresentable
    # rather than mitigated (ADR-0011).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # The type determined by inspecting bytes, never the client's claim.
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # Kept as data for display and Content-Disposition, never as a path segment.
    original_filename: Mapped[str] = mapped_column(String(MAX_FILENAME_LENGTH), nullable=False)

    status: Mapped[ProcessingStatus] = mapped_column(
        processing_status_enum, nullable=False, server_default=text("'pending'")
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document: Mapped[Document] = relationship(back_populates="versions", lazy="raise")

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_document_versions_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_document_versions_document_within_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version_number"
        ),
        # At most one current version per document, and the index that serves
        # the join from `documents` to its current content.
        Index(
            "uq_document_versions_current",
            "document_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # Deduplication (ADR-0011), scoped to the workspace and to *current*
        # content. A workspace-wide constraint across all versions would reject
        # the legitimate case of reverting a document to earlier content, since
        # the superseded row still holds that hash.
        Index(
            "uq_document_versions_workspace_content_current",
            "workspace_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # The status poll. Partial because ready and failed rows dominate and
        # are never polled, so the index stays small and hot.
        Index(
            "ix_document_versions_workspace_id_status",
            "workspace_id",
            "status",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        # Reverse lookup for the orphaned-object sweep, which starts from
        # storage and asks whether a row still references each key.
        Index("ix_document_versions_storage_key", "storage_key"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256_is_hex"),
        CheckConstraint("chunk_count >= 0", name="chunk_count_non_negative"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="page_count_positive"),
        # Ambiguous states are unrepresentable: a failure must say why.
        CheckConstraint(
            "(status = 'failed') = (failure_code IS NOT NULL)",
            name="failed_versions_have_a_code",
        ),
        # A READY version with no chunks answers no questions while looking
        # perfectly healthy in a list. ADR-0012 makes that case FAILED with a
        # distinct reason; this constraint is what enforces that decision.
        CheckConstraint("status <> 'ready' OR chunk_count > 0", name="ready_versions_have_chunks"),
        CheckConstraint(
            "(status IN ('ready', 'failed')) = (processed_at IS NOT NULL)",
            name="terminal_versions_have_processed_at",
        ),
    )


class Tag(UUIDPrimaryKeyMixin, TimestampMixin, OptimisticVersionMixin, Base):
    """A workspace-scoped label.

    Versioned because a tag is renamed and recoloured by more than one person,
    and a silent last-writer-wins on a name everyone sees is exactly the lost
    update the version column exists to stop.
    """

    __tablename__ = "tags"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Restricted to a named palette by the application; stored as a token rather
    # than a hex value so the theme can change without rewriting rows.
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_tags_workspace_id_id"),
        Index(
            "uq_tags_workspace_id_name_lower",
            "workspace_id",
            text("lower(name)"),
            unique=True,
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )


class DocumentTag(Base):
    """Join between a document and a tag.

    Both foreign keys are composite and include `workspace_id`, so tagging a
    document with another tenant's tag is rejected by the database rather than
    by a check in application code.
    """

    __tablename__ = "document_tags"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_document_tags_document_within_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tag_id"],
            ["tags.workspace_id", "tags.id"],
            name="fk_document_tags_tag_within_workspace",
            ondelete="CASCADE",
        ),
        # "Which documents carry this tag?" -- the primary key serves the
        # opposite direction, so this covers filtering a list by tag.
        Index("ix_document_tags_workspace_id_tag_id", "workspace_id", "tag_id"),
    )


class Chunk(UUIDPrimaryKeyMixin, Base):
    """A retrievable passage of one document version.

    Append-only and derived: chunks are never edited, and are deleted and
    rebuilt wholesale when a version is reprocessed. That is why they carry no
    `updated_at`, no optimistic `version`, and no soft delete.

    The embedding lives here rather than in a separate `embeddings` table. One
    chunk has exactly one embedding in the model, so a second table would add a
    join to the hottest query in the system to express a relationship that is
    not actually one-to-many. Migrating to a new embedding model is an
    expand/contract column change (ADR-0007), not a second row.
    """

    __tablename__ = "chunks"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Provenance. Without these a chunk cannot be cited, which makes it useless
    # (ADR-0006).
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # SHA-256 of `content`. Lets "did reprocessing produce the same chunks"
    # be a comparison rather than a text diff (ADR-0013).
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Every chunk row is an *indexed* chunk: the vector and the full identity
    # of the space it lives in are written with the text, in the same
    # statement, or the row does not exist (migration 0004, ADR-0020). "READY
    # but not embedded" is therefore unrepresentable rather than merely
    # avoided -- a READY version has chunks, and every chunk has a vector.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    # The space: which model, at which width. Retrieval filters on both, so a
    # vector from any other space is never compared with a query vector.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    # SHA-256 of exactly what was embedded -- heading path and text
    # (`orbit.domain.embeddings.build_embedding_input`). Equal hashes in one
    # space mean equal vectors, which is what lets a reprocess or a new
    # version reuse vectors instead of paying for them again.
    embedding_input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # When the provider produced the vector. Carried over when a vector is
    # reused, so it can be older than the row.
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Recorded per row so that "which documents need re-chunking" is a query
    # rather than a guess (ADR-0013).
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Generated by the database, so the lexical index can never drift from the
    # text it indexes. `english` is fixed for now; `document_versions.language`
    # exists so it can become per-document without a schema change.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            name="fk_chunks_version_within_workspace",
            ondelete="CASCADE",
        ),
        # `document_id` is denormalised so that reclaiming a document's chunks
        # is one statement. Constraining it as well costs one index probe per
        # insert and removes the possibility of a chunk pointing at a document
        # its version does not belong to.
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_chunks_document_within_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_chunks_document_version_id_ordinal"
        ),
        # Dense retrieval (ADR-0005). Cosine distance, matching how the
        # embeddings are normalised.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Lexical retrieval.
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        # Two queries, one index. The tenant pre-filter both retrievers apply
        # (HNSW cannot include a scalar column, so this is what makes the
        # workspace predicate selective), and embedding reuse, which looks up
        # vectors by input hash *within* a workspace.
        Index(
            "ix_chunks_workspace_id_embedding_input_sha256",
            "workspace_id",
            "embedding_input_sha256",
        ),
        # Index coverage and the re-index sweep: "how many chunks are in each
        # space" and "which are not in the active one". Low cardinality, so
        # B-tree deduplication keeps it a small fraction of the table.
        Index("ix_chunks_embedding_space", "embedding_model", "embedding_dimensions"),
        Index("ix_chunks_document_id", "document_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        CheckConstraint("char_end > char_start", name="char_range_is_forward"),
        CheckConstraint("char_start >= 0", name="char_start_non_negative"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256_is_hex"),
        CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_to >= page_from",
            name="page_range_is_forward",
        ),
        # The recorded width is the vector's actual width -- metadata that
        # cannot drift from the data it describes.
        CheckConstraint(
            "vector_dims(embedding) = embedding_dimensions",
            name="embedding_dimensions_match_vector",
        ),
        CheckConstraint("length(btrim(embedding_model)) > 0", name="embedding_model_not_blank"),
        CheckConstraint(
            "length(embedding_input_sha256) = 64", name="embedding_input_sha256_is_hex"
        ),
    )


class DocumentProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt at processing a version.

    A row per attempt rather than a mutable counter, because the question worth
    answering during an incident is "what happened on each try" -- which error,
    how long, under which request id.

    The row, not the broker message, is the source of truth for whether work
    remains (ADR-0019). A RUNNING row is held under a **lease**: `worker_id`
    names the one execution allowed to write its outcome, and
    `lease_expires_at` is when recovery may conclude that execution is dead.
    """

    __tablename__ = "document_processing_jobs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        job_status_enum, nullable=False, server_default=text("'queued'")
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Position within the current processing run; the retry budget is measured
    # against this, so an explicit reprocess starts a fresh budget while
    # `attempt` keeps numbering the version's whole history uniquely.
    run_attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # Not before. Backoff lives here, durably, rather than only as a broker
    # countdown that a Redis restart would forget.
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # When a message for this job was last published. Recovery re-publishes
    # only for jobs whose last publish is older than a grace period, so a
    # backlog does not multiply into duplicate messages on every sweep.
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The lease. Set together, cleared together (see the check constraint).
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The last pipeline stage this attempt began. Survives the worker, so an
    # abandoned job still says where it died.
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Ties the worker's attempts back to the upload request that started them
    # (ADR-0015).
    request_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_kind: Mapped[FailureKind | None] = mapped_column(failure_kind_enum, nullable=True)
    # Operator detail: exception type and message, bounded. Never shown to a
    # user -- the user-safe reason lives on the version.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            name="fk_jobs_version_within_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "attempt", name="uq_jobs_document_version_id_attempt"
        ),
        # At most one live job per version. This is the idempotency guarantee
        # for *creating* work: a double-submitted reprocess, or a retry racing
        # recovery, collides here instead of processing the same bytes twice.
        Index(
            "uq_jobs_active_per_version",
            "document_version_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        # Recovery sweep, part one: queued jobs whose message may have been
        # lost. Redis is disposable, so the queue is not treated as reliable
        # (docs/architecture/data-flow.md).
        Index(
            "ix_jobs_queued_scheduled_for",
            "scheduled_for",
            postgresql_where=text("status = 'queued'"),
        ),
        # Recovery sweep, part two: running jobs whose worker stopped renewing.
        Index(
            "ix_jobs_running_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_jobs_document_version_id", "document_version_id"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "run_attempt >= 1 AND run_attempt <= attempt", name="run_attempt_within_attempt"
        ),
        # A running job always has exactly one holder and a deadline; nothing
        # else has either. "Running, held by nobody" is unrepresentable.
        CheckConstraint(
            "(status = 'running') = (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="running_jobs_hold_a_lease",
        ),
        # A failure must say what and what kind; nothing else may claim one.
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL AND failure_kind IS NOT NULL)",
            name="failed_jobs_are_classified",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL", name="finished_implies_started"
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="terminal_jobs_have_finished_at",
        ),
    )
