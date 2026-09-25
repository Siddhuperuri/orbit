"""ORM base, naming conventions, and shared column mixins.

Two things here are load-bearing beyond convenience:

*   **The naming convention.** Without it, PostgreSQL invents constraint names
    and Alembic autogenerate cannot match an existing constraint to a model one,
    so it proposes dropping and recreating constraints on unrelated migrations.
    Deterministic names also mean a violation in a log names something greppable.

*   **`lazy="raise"` on every relationship.** SQLAlchemy has no global
    default for this, so it is declared per relationship and enforced by
    `tests/unit/test_orm_metadata.py`, which walks every mapper and fails if one
    is missing. An unloaded relationship access then raises instead of silently
    emitting a query, so an N+1 fails a test rather than becoming a production
    incident (ADR-0010).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from orbit.core.ids import new_uuid7

# `ix` deliberately includes the column list so that two indexes on the same
# table cannot collide, which the more common `ix_%(table_name)s_%(column_0_name)s`
# permits.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""

    metadata = metadata

    def __repr__(self) -> str:
        # Identity only. A default repr that prints every column would put
        # document text and password hashes into tracebacks and debugger output.
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """Time-ordered UUID primary key.

    Generated in Python rather than by the database so that a caller can build a
    whole object graph -- document, version, chunks -- in memory and insert it in
    one round trip, without a `RETURNING` round trip per row to learn each id.
    """

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=new_uuid7)


class TimestampMixin:
    """Database-generated creation and update timestamps.

    Defaults are server-side (`now()`), not Python-side: a clock-skewed
    application host must not be able to write timestamps that disagree with the
    database's own ordering, and rows written by a migration or by `psql` get
    correct values too.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # SQLAlchemy issues this on UPDATE. A database trigger would also cover
        # manual SQL, but a trigger is invisible in the model and easy to forget
        # when adding a table; this is explicit and lives with the schema.
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Recoverable deletion for user-facing entities.

    Documents, folders, and workspaces are soft-deleted so that an accidental
    delete of someone's corpus is recoverable. Derived data -- versions, chunks,
    embeddings -- is hard-cascaded instead, because it is rebuildable and
    worthless without its parent (docs/architecture/data-flow.md).

    Every query filters `deleted_at IS NULL` by default; including deleted rows
    is explicit and restricted to administrative paths.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class OptimisticVersionMixin:
    """Lost-update protection for concurrently edited entities.

    SQLAlchemy adds `WHERE version = :expected` to every UPDATE and raises
    `StaleDataError` when no row matches. Without this, two users renaming a
    document at the same time silently produces last-writer-wins, and the first
    user's change vanishes with no error anywhere.

    Applied only where concurrent edit is realistic. Append-only tables such as
    `audit_logs` and `chunks` do not carry it.
    """

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 -- declarative directive
        # Must be a declared_attr: a plain dict on a mixin is evaluated before
        # the column exists, so `version_id_col` would bind to nothing and the
        # protection would silently not apply.
        return {"version_id_col": cls.version}
