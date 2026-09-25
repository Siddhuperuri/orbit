"""Vector index: every chunk is embedded, and knows which space it is in.

Revision ID: 0004_vector_index
Revises: 0003_processing_pipeline
Create Date: 2026-09-17

ADR-0020. Until now a chunk's vector was nullable and described only by a
model name. This makes the stored vector self-describing and mandatory:

* `embedding`, `embedding_model` become NOT NULL. A chunk row is an indexed
  chunk or it does not exist, so "READY but not embedded" is unrepresentable.
* `embedding_dimensions` records the space's width, with a CHECK that it is
  the vector's actual width.
* `embedding_input_sha256` identifies exactly what was embedded (heading path
  plus text), so identical inputs reuse vectors instead of being paid for
  again.
* `embedded_at` records when the provider produced the vector.
* `ix_chunks_workspace_id` becomes `(workspace_id, embedding_input_sha256)`:
  the same tenant pre-filter, and the reuse lookup.
* `ix_chunks_embedding_space` serves the coverage report and re-index sweep.

Existing rows are backfilled exactly rather than approximately: the width
from `vector_dims`, the input hash recomputed in SQL with the same rule as
`orbit.domain.embeddings.build_embedding_input`, and `embedded_at` from the
version's `processed_at` -- chunks are written in the transaction that marks
the version READY, so that is when their vectors were stored.

The backfill rewrites every chunk row. That is fine before a production corpus
exists; on a large table, run it in a maintenance window.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_vector_index"
down_revision: str | None = "0003_processing_pipeline"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    unembedded = bind.execute(
        sa.text("SELECT count(*) FROM chunks WHERE embedding IS NULL OR embedding_model IS NULL")
    ).scalar_one()
    if unembedded:
        # No code path has written such a row; if one exists it was written by
        # hand, and inventing a vector for it is not this migration's call.
        msg = (
            f"chunks holds {unembedded} rows without an embedding. Delete them or "
            "reprocess their documents before upgrading."
        )
        raise RuntimeError(msg)

    op.add_column("chunks", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))
    op.add_column(
        "chunks", sa.Column("embedding_input_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column("chunks", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))

    # `E'\n\n'` and the empty-string test mirror `build_embedding_input`
    # exactly; tests/integration/test_vector_index_migration.py asserts the
    # two produce the same hash.
    op.execute(
        sa.text(
            """
            UPDATE chunks AS c
            SET embedding_dimensions = vector_dims(c.embedding),
                embedding_input_sha256 = encode(
                    sha256(convert_to(
                        CASE
                            WHEN c.heading_path IS NULL OR c.heading_path = '' THEN c.content
                            ELSE c.heading_path || E'\\n\\n' || c.content
                        END,
                        'UTF8'
                    )),
                    'hex'
                ),
                embedded_at = coalesce(v.processed_at, now())
            FROM document_versions AS v
            WHERE v.id = c.document_version_id
            """
        )
    )

    for column in (
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_input_sha256",
        "embedded_at",
    ):
        op.alter_column("chunks", column, nullable=False)

    op.drop_constraint(op.f("ck_chunks_embedding_and_model_agree"), "chunks", type_="check")
    op.create_check_constraint(
        op.f("ck_chunks_embedding_dimensions_match_vector"),
        "chunks",
        "vector_dims(embedding) = embedding_dimensions",
    )
    op.create_check_constraint(
        op.f("ck_chunks_embedding_model_not_blank"),
        "chunks",
        "length(btrim(embedding_model)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_chunks_embedding_input_sha256_is_hex"),
        "chunks",
        "length(embedding_input_sha256) = 64",
    )

    op.drop_index("ix_chunks_workspace_id", table_name="chunks")
    op.create_index(
        "ix_chunks_workspace_id_embedding_input_sha256",
        "chunks",
        ["workspace_id", "embedding_input_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_chunks_embedding_space",
        "chunks",
        ["embedding_model", "embedding_dimensions"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding_space", table_name="chunks")
    op.drop_index("ix_chunks_workspace_id_embedding_input_sha256", table_name="chunks")
    op.create_index("ix_chunks_workspace_id", "chunks", ["workspace_id"], unique=False)

    op.drop_constraint(op.f("ck_chunks_embedding_input_sha256_is_hex"), "chunks", type_="check")
    op.drop_constraint(op.f("ck_chunks_embedding_model_not_blank"), "chunks", type_="check")
    op.drop_constraint(op.f("ck_chunks_embedding_dimensions_match_vector"), "chunks", type_="check")
    op.create_check_constraint(
        op.f("ck_chunks_embedding_and_model_agree"),
        "chunks",
        "(embedding IS NULL) = (embedding_model IS NULL)",
    )
    op.alter_column("chunks", "embedding_model", nullable=True)
    op.alter_column("chunks", "embedding", nullable=True)

    op.drop_column("chunks", "embedded_at")
    op.drop_column("chunks", "embedding_input_sha256")
    op.drop_column("chunks", "embedding_dimensions")
