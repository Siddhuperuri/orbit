"""Document organisation: archive, list orderings, title filter, versioned tags.

Revision ID: 0006_document_organization
Revises: 0005_grounded_answers
Create Date: 2026-09-19

* `documents.archived_at` -- archive is a visibility state, not a lifecycle one:
  nothing else about the document changes, so restoring is instant. Nullable, so
  every existing document is (truthfully) not archived with no backfill.
* Three indexes so each list ordering pages by walking an index instead of
  sorting the workspace: recently updated, title, and the archive view.
* `ix_documents_title_trgm` -- the list's title filter is a case-insensitive
  substring match, which only a trigram index can serve. Requires the `pg_trgm`
  extension, which provisioning creates (`docker/postgres/initdb`); this
  migration cannot, because the application role must not hold the privilege.
  If the extension is missing the migration fails here, at the statement that
  needs it, rather than later as a slow query.
* `tags.version` -- tags are renamed and recoloured by several people, so they
  get the same lost-update protection documents and folders have. Existing rows
  start at 1.

Purely additive; nothing is rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_document_organization"
down_revision: str | None = "0005_grounded_answers"
branch_labels: str | None = None
depends_on: str | None = None

_LIVE = sa.text("deleted_at IS NULL")


def upgrade() -> None:
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tags",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )

    op.create_index(
        "ix_documents_workspace_id_updated_at_id",
        "documents",
        ["workspace_id", sa.text("updated_at DESC"), sa.text("id DESC")],
        unique=False,
        postgresql_where=_LIVE,
    )
    op.create_index(
        "ix_documents_workspace_id_lower_title_id",
        "documents",
        ["workspace_id", sa.text("lower(title)"), "id"],
        unique=False,
        postgresql_where=_LIVE,
    )
    op.create_index(
        "ix_documents_workspace_id_created_at_id_archived",
        "documents",
        ["workspace_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND archived_at IS NOT NULL"),
    )
    op.create_index(
        "ix_documents_title_trgm",
        "documents",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
        postgresql_where=_LIVE,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_title_trgm", table_name="documents")
    op.drop_index("ix_documents_workspace_id_created_at_id_archived", table_name="documents")
    op.drop_index("ix_documents_workspace_id_lower_title_id", table_name="documents")
    op.drop_index("ix_documents_workspace_id_updated_at_id", table_name="documents")
    op.drop_column("tags", "version")
    op.drop_column("documents", "archived_at")
