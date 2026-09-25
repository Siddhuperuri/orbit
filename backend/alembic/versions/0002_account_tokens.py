"""Account tokens: password reset and email verification.

Revision ID: 0002_account_tokens
Revises: 0001_initial_schema
Create Date: 2026-09-10

One table for both flows. They are the same mechanism -- a hashed, single-use,
expiring secret mailed to an address -- differing only in what redeeming one
does, and the `purpose` column is checked at redemption so a verification
token can never be spent as a password reset. Two tables would duplicate the
issue/consume/expire logic and, with it, the opportunity to get that logic
subtly different in one of them.

Only the SHA-256 hash of each token is stored, exactly as for refresh tokens
(ADR-0003): a read of this table yields nothing redeemable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_account_tokens"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "account_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum("password_reset", "email_verification", name="account_token_purpose"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_account_tokens_expires_after_creation")
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64", name=op.f("ck_account_tokens_token_hash_is_sha256_hex")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_account_tokens_token_hash"),
    )
    op.create_index("ix_account_tokens_expires_at", "account_tokens", ["expires_at"], unique=False)
    op.create_index(
        "ix_account_tokens_user_id_purpose",
        "account_tokens",
        ["user_id", "purpose"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the table and its enum type.

    `DROP TABLE` leaves a native enum type behind, so the next upgrade would
    fail with "type already exists" -- the exact failure a
    rollback-then-roll-forward hits. Verified by the migration integration
    suite, which runs upgrade -> downgrade -> upgrade.
    """
    op.drop_table("account_tokens")
    sa.Enum(name="account_token_purpose").drop(op.get_bind(), checkfirst=True)
