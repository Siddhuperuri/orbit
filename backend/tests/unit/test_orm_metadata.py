"""Schema invariants, asserted against the mapper metadata.

These run with no database and catch the mistakes that are easy to make when
adding a table and expensive to discover later: a relationship that lazy-loads,
a tenant-scoped table missing its `workspace_id`, a timestamp that silently
drops its timezone, or an unbounded text column on a user-supplied field.

They are cheap, they run in milliseconds, and each one encodes a decision from
docs/architecture/backend.md rather than a stylistic preference.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.orm import class_mapper

from orbit.infrastructure.db.models import Base
from orbit.infrastructure.db.models.base import NAMING_CONVENTION

ALL_TABLES = sorted(Base.metadata.tables)

# Tables that live inside a workspace and must therefore carry `workspace_id`.
# Anything not listed here is account-level or historical, and its absence from
# this set is a deliberate statement.
NON_TENANT_TABLES = {
    "users",
    "workspaces",
    "refresh_tokens",
    # A password-reset or verification token belongs to an account, which
    # exists before -- and independently of -- any workspace membership.
    "account_tokens",
    # Audit records are historical assertions, not tenant-scoped rows; their
    # workspace_id is nullable and unconstrained on purpose (see audit.py).
    "audit_logs",
}


def _mapped_classes() -> list[type]:
    return [mapper.class_ for mapper in Base.registry.mappers]


def test_every_table_is_registered() -> None:
    assert len(ALL_TABLES) == 16, f"expected 16 tables, found {len(ALL_TABLES)}: {ALL_TABLES}"


@pytest.mark.parametrize("model", _mapped_classes(), ids=lambda m: m.__name__)
def test_every_relationship_forbids_lazy_loading(model: type) -> None:
    """`lazy="raise"` is what turns an N+1 into a test failure.

    SQLAlchemy has no global default for this, so a relationship added without
    it would silently reintroduce implicit queries. This is the enforcement
    (ADR-0010).
    """
    for relationship in class_mapper(model).relationships:
        assert relationship.lazy == "raise", (
            f"{model.__name__}.{relationship.key} may lazy-load "
            f"(lazy={relationship.lazy!r}); set lazy='raise'"
        )


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_tenant_scoped_tables_carry_workspace_id(table_name: str) -> None:
    """Every tenant-scoped table must be filterable by workspace in one predicate.

    Without the column, isolating that table would require a join, and a join
    is something a query can forget (ADR-0004).
    """
    if table_name in NON_TENANT_TABLES:
        return
    columns = Base.metadata.tables[table_name].columns
    assert "workspace_id" in columns, f"{table_name} is tenant-scoped but has no workspace_id"


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_every_timestamp_is_timezone_aware(table_name: str) -> None:
    """A naive timestamp column silently discards the offset it was given."""
    for column in Base.metadata.tables[table_name].columns:
        if isinstance(column.type, DateTime):
            assert column.type.timezone, (
                f"{table_name}.{column.name} is TIMESTAMP WITHOUT TIME ZONE"
            )


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_every_table_has_a_primary_key(table_name: str) -> None:
    assert Base.metadata.tables[table_name].primary_key.columns, f"{table_name} has no primary key"


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_constraint_names_follow_the_convention(table_name: str) -> None:
    """Deterministic names are what let the error translator map a violation to
    a user-facing message, and what stop autogenerate from proposing spurious
    drop-and-recreate churn."""
    table = Base.metadata.tables[table_name]
    for constraint in table.constraints:
        assert constraint.name, f"{table_name} has an unnamed {type(constraint).__name__}"
        assert not str(constraint.name).startswith("_unnamed_"), (
            f"{table_name}.{constraint.name} was auto-named"
        )
    for index in table.indexes:
        assert index.name, f"{table_name} has an unnamed index"


def test_naming_convention_covers_every_constraint_kind() -> None:
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_user_supplied_text_is_length_bounded(table_name: str) -> None:
    """Unbounded `TEXT` on a user-supplied field is a storage-exhaustion vector.

    The exceptions are content columns, where the size limit is enforced
    upstream by the upload cap or by the chunker.
    """
    unbounded_by_design = {
        ("users", "password_hash"),  # Argon2 encodes its parameters; length varies
        ("chunks", "content"),  # bounded by the chunker's token limit
        ("chunks", "heading_path"),
        ("message_citations", "heading_path"),  # a snapshot of chunks.heading_path
        ("messages", "content"),  # bounded by the model's output cap
        ("document_versions", "failure_reason"),  # generated by us, not by a user
        ("document_processing_jobs", "error_message"),
    }
    for column in Base.metadata.tables[table_name].columns:
        # Text subclasses String, so this matches TEXT columns only -- a
        # VARCHAR(n) column is a String but not a Text.
        if isinstance(column.type, Text):
            assert (table_name, column.name) in unbounded_by_design, (
                f"{table_name}.{column.name} is unbounded TEXT; bound it or "
                f"document why it is exempt"
            )


class TestDocumentVersioning:
    """The version model's guarantees, expressed as index assertions."""

    def test_at_most_one_current_version_per_document(self) -> None:
        index = _index("document_versions", "uq_document_versions_current")
        assert index.unique
        assert index.dialect_options["postgresql"]["where"] is not None

    def test_deduplication_is_scoped_to_workspace_and_current_content(self) -> None:
        index = _index("document_versions", "uq_document_versions_workspace_content_current")
        assert index.unique
        assert [c.name for c in index.columns] == ["workspace_id", "content_sha256"]
        # Partial on `is_current`: a workspace-wide constraint across every
        # version would reject reverting a document to earlier content.
        assert index.dialect_options["postgresql"]["where"] is not None

    def test_version_numbers_are_unique_per_document(self) -> None:
        table = Base.metadata.tables["document_versions"]
        names = {c.name for c in table.constraints}
        assert "uq_document_versions_document_id_version_number" in names


class TestRetrievalIndexes:
    def test_dense_retrieval_uses_hnsw_with_cosine(self) -> None:
        index = _index("chunks", "ix_chunks_embedding_hnsw")
        options = index.dialect_options["postgresql"]
        assert options["using"] == "hnsw"
        assert options["ops"] == {"embedding": "vector_cosine_ops"}

    def test_lexical_retrieval_uses_gin(self) -> None:
        index = _index("chunks", "ix_chunks_search_vector")
        assert index.dialect_options["postgresql"]["using"] == "gin"

    def test_workspace_filter_is_indexed(self) -> None:
        """HNSW cannot include a scalar column, so the tenant pre-filter needs
        its own btree or it degrades to a scan (ADR-0005). Its leading column
        is the workspace; the second serves embedding reuse (ADR-0020)."""
        index = _index("chunks", "ix_chunks_workspace_id_embedding_input_sha256")
        assert [column.name for column in index.columns] == [
            "workspace_id",
            "embedding_input_sha256",
        ]

    def test_every_chunk_carries_a_vector_and_its_space(self) -> None:
        """A chunk row without a vector, or without the identity of the space
        its vector lives in, is unrepresentable (migration 0004)."""
        chunks = Base.metadata.tables["chunks"]
        for name in (
            "embedding",
            "embedding_model",
            "embedding_dimensions",
            "embedding_input_sha256",
            "embedded_at",
        ):
            assert chunks.c[name].nullable is False, name

    def test_the_recorded_width_is_checked_against_the_vector(self) -> None:
        chunks = Base.metadata.tables["chunks"]
        checks = {
            str(constraint.sqltext)
            for constraint in chunks.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert "vector_dims(embedding) = embedding_dimensions" in checks

    def test_document_list_index_matches_the_keyset_order(self) -> None:
        index = _index("documents", "ix_documents_workspace_id_created_at_id")
        assert index.dialect_options["postgresql"]["where"] is not None


class TestCrossTenantForeignKeys:
    """Composite foreign keys that make a cross-workspace reference impossible.

    A plain `tag_id` FK would permit tagging a document with another tenant's
    tag; including `workspace_id` in the key means the database rejects it.
    """

    @pytest.mark.parametrize(
        ("table_name", "constraint_name"),
        [
            ("documents", "fk_documents_folder_within_workspace"),
            ("document_versions", "fk_document_versions_document_within_workspace"),
            ("document_tags", "fk_document_tags_document_within_workspace"),
            ("document_tags", "fk_document_tags_tag_within_workspace"),
            ("chunks", "fk_chunks_version_within_workspace"),
            ("chunks", "fk_chunks_document_within_workspace"),
            ("messages", "fk_messages_conversation_within_workspace"),
            ("message_citations", "fk_message_citations_document_within_workspace"),
        ],
    )
    def test_composite_key_includes_workspace(self, table_name: str, constraint_name: str) -> None:
        table = Base.metadata.tables[table_name]
        constraint = next(
            (c for c in table.foreign_key_constraints if c.name == constraint_name), None
        )
        assert constraint is not None, f"{constraint_name} is missing from {table_name}"
        assert "workspace_id" in {c.name for c in constraint.columns}


class TestDeletionStrategy:
    @pytest.mark.parametrize(
        "table_name", ["users", "workspaces", "documents", "folders", "conversations"]
    )
    def test_user_facing_entities_are_soft_deleted(self, table_name: str) -> None:
        assert "deleted_at" in Base.metadata.tables[table_name].columns

    @pytest.mark.parametrize(
        "table_name", ["chunks", "document_versions", "workspace_members", "messages"]
    )
    def test_derived_and_access_rows_are_hard_deleted(self, table_name: str) -> None:
        """Derived data is rebuildable, and a soft-deleted membership row is one
        forgotten filter away from a privilege-escalation bug."""
        assert "deleted_at" not in Base.metadata.tables[table_name].columns

    def test_chunks_cascade_from_their_version(self) -> None:
        table = Base.metadata.tables["chunks"]
        constraint = next(
            c
            for c in table.foreign_key_constraints
            if c.name == "fk_chunks_version_within_workspace"
        )
        assert constraint.ondelete == "CASCADE"

    def test_document_creator_reference_survives_account_deletion(self) -> None:
        """A document belongs to the workspace, not to the person who uploaded
        it, so purging an account must not delete the workspace's content."""
        table = Base.metadata.tables["documents"]
        constraint = next(
            c
            for c in table.foreign_key_constraints
            if "created_by_user_id" in {col.name for col in c.columns}
        )
        assert constraint.ondelete == "SET NULL"


class TestOptimisticConcurrency:
    @pytest.mark.parametrize("table_name", ["documents", "workspaces", "folders", "conversations"])
    def test_concurrently_edited_entities_have_a_version_column(self, table_name: str) -> None:
        column = Base.metadata.tables[table_name].columns.get("version")
        assert column is not None, f"{table_name} supports concurrent edit but has no version"
        assert isinstance(column.type, Integer)

    @pytest.mark.parametrize("table_name", ["chunks", "audit_logs", "messages"])
    def test_append_only_tables_have_no_version_column(self, table_name: str) -> None:
        assert "version" not in Base.metadata.tables[table_name].columns


def test_audit_log_has_no_foreign_keys() -> None:
    """Deliberate. A foreign key would either block a purge or delete the record
    of that purge; both defeat the purpose of an audit trail (see audit.py)."""
    assert not Base.metadata.tables["audit_logs"].foreign_key_constraints


def _index(table_name: str, index_name: str) -> Index:
    table = Base.metadata.tables[table_name]
    index = next((i for i in table.indexes if i.name == index_name), None)
    assert index is not None, f"{index_name} is missing from {table_name}"
    return index
