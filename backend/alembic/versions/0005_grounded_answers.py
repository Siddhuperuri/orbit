"""Grounded answers: message lifecycle, measurements, richer citation snapshots.

Revision ID: 0005_grounded_answers
Revises: 0004_vector_index
Create Date: 2026-09-18

ADR-0022. An assistant message now has a lifecycle -- inserted `pending` when
its question is accepted, moved once to `complete`, `partial`, or `failed` --
and records how well it is grounded, why it stopped, which stage failed, and
how long each stage took.

* `status` defaults to `complete` so existing rows (written whole, before
  answers had a lifecycle) are described truthfully without a backfill.
* `uq_messages_one_pending_per_conversation` makes "one answer in flight per
  conversation" a database invariant rather than a convention.
* Citations gain `version_number`, `chunk_ordinal`, and `heading_path`, so a
  snapshot still reads "v3, section 2.1" after the version and chunk are gone.

Purely additive; nothing is rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_grounded_answers"
down_revision: str | None = "0004_vector_index"
branch_labels: str | None = None
depends_on: str | None = None

_STATUS = sa.Enum("pending", "complete", "partial", "failed", name="message_status")
_GROUNDING = sa.Enum(
    "grounded", "uncited", "insufficient_evidence", "no_evidence", name="answer_grounding"
)


def upgrade() -> None:
    bind = op.get_bind()
    _STATUS.create(bind, checkfirst=True)
    _GROUNDING.create(bind, checkfirst=True)

    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "complete",
                "partial",
                "failed",
                name="message_status",
                create_type=False,
            ),
            server_default=sa.text("'complete'"),
            nullable=False,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "grounding",
            sa.Enum(
                "grounded",
                "uncited",
                "insufficient_evidence",
                "no_evidence",
                name="answer_grounding",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    for name, column_type in (
        ("stop_reason", sa.String(length=32)),
        ("failure_stage", sa.String(length=16)),
        ("failure_code", sa.String(length=64)),
        ("prompt_version", sa.String(length=64)),
        ("request_id", sa.String(length=64)),
        ("retrieval_ms", sa.Integer()),
        ("generation_ms", sa.Integer()),
        ("total_ms", sa.Integer()),
        ("first_token_ms", sa.Integer()),
        ("retrieved_count", sa.Integer()),
        ("context_tokens", sa.Integer()),
        ("retrieval_degraded", sa.String(length=64)),
    ):
        op.add_column("messages", sa.Column(name, column_type, nullable=True))

    op.create_check_constraint(
        op.f("ck_messages_completion_tokens_non_negative"),
        "messages",
        "completion_tokens IS NULL OR completion_tokens >= 0",
    )
    op.create_check_constraint(
        op.f("ck_messages_user_messages_are_complete"),
        "messages",
        "role = 'assistant' OR (status = 'complete' AND grounding IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_messages_failed_messages_say_why"),
        "messages",
        "status <> 'failed' OR failure_code IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_messages_failure_stage_known"),
        "messages",
        "failure_stage IS NULL OR failure_stage IN ('retrieval', 'generation')",
    )
    op.create_index(
        "uq_messages_one_pending_per_conversation",
        "messages",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.add_column("message_citations", sa.Column("version_number", sa.Integer(), nullable=True))
    op.add_column("message_citations", sa.Column("chunk_ordinal", sa.Integer(), nullable=True))
    op.add_column("message_citations", sa.Column("heading_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("message_citations", "heading_path")
    op.drop_column("message_citations", "chunk_ordinal")
    op.drop_column("message_citations", "version_number")

    op.drop_index("uq_messages_one_pending_per_conversation", table_name="messages")
    for name in (
        "ck_messages_failure_stage_known",
        "ck_messages_failed_messages_say_why",
        "ck_messages_user_messages_are_complete",
        "ck_messages_completion_tokens_non_negative",
    ):
        op.drop_constraint(op.f(name), "messages", type_="check")
    for name in (
        "retrieval_degraded",
        "context_tokens",
        "retrieved_count",
        "first_token_ms",
        "total_ms",
        "generation_ms",
        "retrieval_ms",
        "request_id",
        "prompt_version",
        "failure_code",
        "failure_stage",
        "stop_reason",
        "grounding",
        "status",
    ):
        op.drop_column("messages", name)

    bind = op.get_bind()
    _GROUNDING.drop(bind, checkfirst=True)
    _STATUS.drop(bind, checkfirst=True)
