"""Processing pipeline: job leases, retry scheduling, failure classification.

Revision ID: 0003_processing_pipeline
Revises: 0002_account_tokens
Create Date: 2026-09-17

`document_processing_jobs` existed from 0001 as a shape; this makes it the
source of truth for asynchronous work (ADR-0019):

* `scheduled_for` / `enqueued_at` -- durable backoff, and the recovery sweep's
  "was a message published recently" question.
* `worker_id` / `lease_expires_at` / `stage` -- the lease that makes a worker
  restart safe, and the last stage an attempt reached.
* `run_attempt` -- the retry budget within one processing run.
* `failure_kind` -- transient / permanent / defect, the classification that
  decides whether a failure is retried.
* `uq_jobs_active_per_version` -- at most one queued-or-running job per version.

`chunks.content_sha256` is added NOT NULL with no default. That is safe only
because no code path wrote a chunk before this milestone; it is asserted rather
than assumed -- the upgrade refuses to run against a table that has rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_processing_pipeline"
down_revision: str | None = "0002_account_tokens"
branch_labels: str | None = None
depends_on: str | None = None

_failure_kind = sa.Enum("transient", "permanent", "defect", name="failure_kind")


def upgrade() -> None:
    bind = op.get_bind()
    existing_chunks = bind.execute(sa.text("SELECT count(*) FROM chunks")).scalar_one()
    if existing_chunks:
        msg = (
            f"chunks holds {existing_chunks} rows; 0003 adds a NOT NULL content hash "
            "with no default. Backfill before upgrading."
        )
        raise RuntimeError(msg)

    _failure_kind.create(bind, checkfirst=True)

    op.add_column(
        "chunks",
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_chunks_content_sha256_is_hex"), "chunks", "length(content_sha256) = 64"
    )

    op.add_column(
        "document_processing_jobs",
        sa.Column("run_attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("worker_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column("stage", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "document_processing_jobs",
        sa.Column(
            "failure_kind",
            sa.Enum("transient", "permanent", "defect", name="failure_kind", create_type=False),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        op.f("ck_document_processing_jobs_run_attempt_within_attempt"),
        "document_processing_jobs",
        "run_attempt >= 1 AND run_attempt <= attempt",
    )
    op.create_check_constraint(
        op.f("ck_document_processing_jobs_running_jobs_hold_a_lease"),
        "document_processing_jobs",
        "(status = 'running') = (worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_document_processing_jobs_failed_jobs_are_classified"),
        "document_processing_jobs",
        "(status = 'failed') = (error_code IS NOT NULL AND failure_kind IS NOT NULL)",
    )

    op.drop_index(
        "ix_jobs_status_created_at",
        table_name="document_processing_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_jobs_active_per_version",
        "document_processing_jobs",
        ["document_version_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_queued_scheduled_for",
        "document_processing_jobs",
        ["scheduled_for"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_jobs_running_lease_expires_at",
        "document_processing_jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jobs_running_lease_expires_at",
        table_name="document_processing_jobs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(
        "ix_jobs_queued_scheduled_for",
        table_name="document_processing_jobs",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_index(
        "uq_jobs_active_per_version",
        table_name="document_processing_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_status_created_at",
        "document_processing_jobs",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.drop_constraint(
        op.f("ck_document_processing_jobs_failed_jobs_are_classified"),
        "document_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_document_processing_jobs_running_jobs_hold_a_lease"),
        "document_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_document_processing_jobs_run_attempt_within_attempt"),
        "document_processing_jobs",
        type_="check",
    )
    for column in (
        "failure_kind",
        "stage",
        "lease_expires_at",
        "worker_id",
        "enqueued_at",
        "scheduled_for",
        "run_attempt",
    ):
        op.drop_column("document_processing_jobs", column)

    op.drop_constraint(op.f("ck_chunks_content_sha256_is_hex"), "chunks", type_="check")
    op.drop_column("chunks", "content_sha256")

    # Dropped explicitly: a leftover enum type makes the next upgrade fail with
    # "type already exists" -- the rollback-then-roll-forward path.
    _failure_kind.drop(op.get_bind(), checkfirst=True)
