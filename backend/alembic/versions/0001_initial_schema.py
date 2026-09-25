"""Initial schema: identity, content, retrieval, conversations, audit.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-09

Creates the full ORBIT domain model in one migration. Every subsequent change is
expand/contract and incremental; this is the only migration permitted to create
a table that a previous release did not know about.

Extensions (`vector`, `pg_trgm`, `uuid-ossp`) are NOT created here. `CREATE
EXTENSION` requires privileges the application role must not hold, so they are
provisioned out of band -- by docker/postgres/initdb locally, and by the database
platform in production. This migration fails loudly if `vector` is absent, which
is the correct outcome: the schema is unimplementable without it.

See docs/database/schema.md for the reasoning behind the model.
"""

from __future__ import annotations

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("request_id", sa.String(length=26), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)",
            name=op.f("ck_audit_logs_resource_reference_complete"),
        ),
        sa.CheckConstraint(
            "length(btrim(action)) > 0", name=op.f("ck_audit_logs_action_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_actor_user_id_created_at",
        "audit_logs",
        ["actor_user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_request_id",
        "audit_logs",
        ["request_id"],
        unique=False,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_logs_resource_type_resource_id",
        "audit_logs",
        ["resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_workspace_id_created_at",
        "audit_logs",
        ["workspace_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("token_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "position('@' in email) > 1", name=op.f("ck_users_email_has_local_part")
        ),
        sa.CheckConstraint(
            "length(btrim(full_name)) > 0", name=op.f("ck_users_full_name_not_blank")
        ),
        sa.CheckConstraint("token_epoch >= 0", name=op.f("ck_users_token_epoch_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "expires_at > issued_at", name=op.f("ck_refresh_tokens_expires_after_issue")
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64", name=op.f("ck_refresh_tokens_token_hash_is_sha256_hex")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False)
    op.create_index(
        "ix_refresh_tokens_user_id_expires_at",
        "refresh_tokens",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name=op.f("ck_workspaces_slug_format")
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_workspaces_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspaces_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index(
        "uq_workspaces_slug_lower",
        "workspaces",
        [sa.literal_column("lower(slug)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "conversations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_conversations_title_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_conversations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_conversations_workspace_id_id"),
    )
    op.create_index(
        "ix_conversations_workspace_id_user_id_created_at",
        "conversations",
        ["workspace_id", "user_id", sa.literal_column("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "folders",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("parent_folder_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("depth", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "(parent_folder_id IS NULL) = (depth = 0)",
            name=op.f("ck_folders_root_folders_have_depth_zero"),
        ),
        sa.CheckConstraint(
            "depth >= 0 AND depth <= 16", name=op.f("ck_folders_depth_within_bounds")
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_folders_name_not_blank")),
        sa.CheckConstraint(
            "parent_folder_id IS NULL OR parent_folder_id <> id",
            name=op.f("ck_folders_no_self_parent"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_folders_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_folder_id"],
            ["folders.id"],
            name=op.f("fk_folders_parent_folder_id_folders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_folders_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_folders")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_folders_workspace_id_id"),
    )
    op.create_index(
        "ix_folders_workspace_id_parent_folder_id",
        "folders",
        ["workspace_id", "parent_folder_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_folders_parent_name",
        "folders",
        ["workspace_id", "parent_folder_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "tags",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_tags_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_tags_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_tags_workspace_id_id"),
    )
    op.create_index(
        "uq_tags_workspace_id_name_lower",
        "tags",
        ["workspace_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "member", "viewer", name="workspace_role"),
            nullable=False,
        ),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_invited_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_members")),
    )
    op.create_index(
        "ix_workspace_members_owners",
        "workspace_members",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.create_index(
        "ix_workspace_members_user_id_workspace_id",
        "workspace_members",
        ["user_id", "workspace_id"],
        unique=False,
    )
    op.create_table(
        "documents",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("folder_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_documents_title_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_documents_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "folder_id"],
            ["folders.workspace_id", "folders.id"],
            name="fk_documents_folder_within_workspace",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_documents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("workspace_id", "id", name="uq_documents_workspace_id_id"),
    )
    op.create_index(
        "ix_documents_workspace_id_created_at_id",
        "documents",
        ["workspace_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_workspace_id_folder_id",
        "documents",
        ["workspace_id", "folder_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "messages",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", name="message_role"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "discarded_citation_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role = 'assistant' OR (model_id IS NULL AND prompt_tokens IS NULL)",
            name=op.f("ck_messages_only_assistant_messages_carry_model_metadata"),
        ),
        sa.CheckConstraint(
            "discarded_citation_count >= 0",
            name=op.f("ck_messages_discarded_citation_count_non_negative"),
        ),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_messages_ordinal_non_negative")),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name=op.f("ck_messages_prompt_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            name="fk_messages_conversation_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id", "ordinal", name="uq_messages_conversation_id_ordinal"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_messages_workspace_id_id"),
    )
    op.create_table(
        "document_tags",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_document_tags_document_within_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tag_id"],
            ["tags.workspace_id", "tags.id"],
            name="fk_document_tags_tag_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "document_id", "tag_id", name=op.f("pk_document_tags")
        ),
    )
    op.create_index(
        "ix_document_tags_workspace_id_tag_id",
        "document_tags",
        ["workspace_id", "tag_id"],
        unique=False,
    )
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "ready", "failed", name="processing_status"),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_code IS NOT NULL)",
            name=op.f("ck_document_versions_failed_versions_have_a_code"),
        ),
        sa.CheckConstraint(
            "(status IN ('ready', 'failed')) = (processed_at IS NOT NULL)",
            name=op.f("ck_document_versions_terminal_versions_have_processed_at"),
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR chunk_count > 0",
            name=op.f("ck_document_versions_ready_versions_have_chunks"),
        ),
        sa.CheckConstraint("byte_size > 0", name=op.f("ck_document_versions_byte_size_positive")),
        sa.CheckConstraint(
            "chunk_count >= 0", name=op.f("ck_document_versions_chunk_count_non_negative")
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name=op.f("ck_document_versions_content_sha256_is_hex")
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name=op.f("ck_document_versions_page_count_positive"),
        ),
        sa.CheckConstraint(
            "version_number >= 1", name=op.f("ck_document_versions_version_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_document_versions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_document_versions_document_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id_version_number"
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_document_versions_workspace_id_id"),
    )
    op.create_index(
        "ix_document_versions_storage_key", "document_versions", ["storage_key"], unique=False
    )
    op.create_index(
        "ix_document_versions_workspace_id_status",
        "document_versions",
        ["workspace_id", "status"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_index(
        "uq_document_versions_current",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "uq_document_versions_workspace_content_current",
        "document_versions",
        ["workspace_id", "content_sha256"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "chunks",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("chunker_version", sa.String(length=32), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name=op.f("ck_chunks_embedding_and_model_agree"),
        ),
        sa.CheckConstraint("char_end > char_start", name=op.f("ck_chunks_char_range_is_forward")),
        sa.CheckConstraint("char_start >= 0", name=op.f("ck_chunks_char_start_non_negative")),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_chunks_ordinal_non_negative")),
        sa.CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_to >= page_from",
            name=op.f("ck_chunks_page_range_is_forward"),
        ),
        sa.CheckConstraint("token_count > 0", name=op.f("ck_chunks_token_count_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_chunks_document_within_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            name="fk_chunks_version_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_chunks_document_version_id_ordinal"
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"], unique=False)
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_search_vector", "chunks", ["search_vector"], unique=False, postgresql_using="gin"
    )
    op.create_index("ix_chunks_workspace_id", "chunks", ["workspace_id"], unique=False)
    op.create_table(
        "document_processing_jobs",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "succeeded", "failed", name="job_status"),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("request_id", sa.String(length=26), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name=op.f("ck_document_processing_jobs_terminal_jobs_have_finished_at"),
        ),
        sa.CheckConstraint(
            "attempt >= 1", name=op.f("ck_document_processing_jobs_attempt_positive")
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name=op.f("ck_document_processing_jobs_finished_implies_started"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["document_versions.workspace_id", "document_versions.id"],
            name="fk_jobs_version_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_processing_jobs")),
        sa.UniqueConstraint(
            "document_version_id", "attempt", name="uq_jobs_document_version_id_attempt"
        ),
    )
    op.create_index(
        "ix_jobs_document_version_id",
        "document_processing_jobs",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_jobs_status_created_at",
        "document_processing_jobs",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "message_citations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("handle", sa.String(length=8), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=True),
        sa.Column("document_title", sa.String(length=512), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("snippet", sa.String(length=2000), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR char_end IS NULL OR char_end > char_start",
            name=op.f("ck_message_citations_char_range_is_forward"),
        ),
        sa.CheckConstraint(
            "length(btrim(snippet)) > 0", name=op.f("ck_message_citations_snippet_not_blank")
        ),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_message_citations_ordinal_non_negative")),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_message_citations_chunk_id_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_message_citations_document_within_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["messages.workspace_id", "messages.id"],
            name="fk_message_citations_message_within_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_citations")),
        sa.UniqueConstraint("message_id", "handle", name="uq_message_citations_message_id_handle"),
    )
    op.create_index(
        "ix_message_citations_message_id", "message_citations", ["message_id"], unique=False
    )
    op.create_index(
        "ix_message_citations_workspace_id_document_id",
        "message_citations",
        ["workspace_id", "document_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Drop everything this migration created.

    Verified by the integration suite, which runs upgrade -> downgrade ->
    upgrade against a real database. A downgrade that leaves an artefact behind
    breaks the second upgrade -- which is exactly the path a production
    rollback-then-roll-forward takes.

    Indexes are dropped implicitly with their tables, so only tables are listed.
    """
    op.drop_table("message_citations")
    op.drop_table("document_processing_jobs")
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("document_tags")
    op.drop_table("messages")
    op.drop_table("documents")
    op.drop_table("workspace_members")
    op.drop_table("tags")
    op.drop_table("folders")
    op.drop_table("conversations")
    op.drop_table("workspaces")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.drop_table("audit_logs")

    # Native enum types outlive their tables: `DROP TABLE` does not remove them,
    # and the next upgrade would fail with "type already exists".
    sa.Enum(name="workspace_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="processing_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="job_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="message_role").drop(op.get_bind(), checkfirst=True)
