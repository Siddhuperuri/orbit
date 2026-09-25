"""Transaction behaviour, pagination, and query plans.

The query-plan tests are unusual and deliberate. An index that exists is not an
index that is *used*: a mismatched sort order, a wrapped column, or a predicate
the planner cannot push down all produce a correct answer via a sequential scan.
These assert that the planner actually chooses the indexes the design depends
on, so a regression shows up here rather than as a slow production query.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from orbit.domain.access import AccessContext
from orbit.domain.documents import DocumentEdit, DocumentListQuery
from orbit.domain.errors import BadRequestError, ConflictError
from orbit.domain.models.entities import (
    Document,
    ProcessingOutcome,
    ProcessingStatus,
    User,
)
from orbit.infrastructure.db.models import User as UserRow
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import unique_email, version_content

pytestmark = pytest.mark.integration


class TestTransactionBoundaries:
    async def test_a_failed_statement_leaves_no_partial_write(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """The whole point of the use case owning the transaction: a document
        and its version are written together or not at all."""
        await uow.documents.create(
            ctx, title="First", folder_id=None, content=version_content("dup")
        )
        # Committed first: a rollback discards the whole transaction, so the
        # "before" state has to be one that a rollback cannot reach.
        await uow.commit()
        before = await uow.session.scalar(
            text("SELECT count(*) FROM documents WHERE workspace_id = :w"),
            {"w": ctx.workspace_id},
        )

        # `ConflictError`, not `IntegrityError`: `create`'s flush is wrapped so
        # the deduplication index's violation always surfaces as a domain
        # error -- the same one a sequential (non-racing) duplicate raises.
        with pytest.raises(ConflictError):
            # Same content hash: rejected by the deduplication index.
            await uow.documents.create(
                ctx, title="Second", folder_id=None, content=version_content("dup")
            )

        await uow.rollback()
        after = await uow.session.scalar(
            text("SELECT count(*) FROM documents WHERE workspace_id = :w"),
            {"w": ctx.workspace_id},
        )
        assert after == before

    async def test_rollback_discards_everything_in_the_transaction(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await uow.documents.create(
            ctx, title="Doomed", folder_id=None, content=version_content("doomed")
        )
        await uow.rollback()

        remaining = await uow.session.scalar(
            text("SELECT count(*) FROM documents WHERE workspace_id = :w"),
            {"w": ctx.workspace_id},
        )
        assert remaining == 0

    async def test_a_constraint_violation_is_translated_at_commit(self, uow: UnitOfWork) -> None:
        """Driver errors carry the statement, its parameters, and the schema.
        They must not escape the data layer (ADR-0014)."""
        email = unique_email()
        await uow.users.create(email=email, password_hash="h", full_name="First")
        # Added to the session rather than executed: an executed statement
        # fails at `execute`, whereas a pending ORM row is flushed by `commit`
        # -- which is the translation path this test exists to cover.
        uow.session.add(UserRow(email=email.upper(), password_hash="h", full_name="Second"))

        with pytest.raises(ConflictError) as caught:
            await uow.commit()

        assert "already exists" in str(caught.value)
        assert "INSERT INTO" not in str(caught.value), "SQL must not reach the caller"

    async def test_version_swap_is_atomic(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        """`uq_document_versions_current` allows one current version, so the
        demote and the insert must be in one transaction -- otherwise a reader
        could observe zero or two."""
        document = await uow.documents.create(
            ctx, title="Doc", folder_id=None, content=version_content("v1")
        )
        await uow.documents.add_version(ctx, document.id, content=version_content("v2"))
        await uow.flush()

        current = await uow.session.scalar(
            text("SELECT count(*) FROM document_versions WHERE document_id = :d AND is_current"),
            {"d": document.id},
        )
        assert current == 1


class TestOptimisticConcurrency:
    async def test_two_renames_produce_a_conflict_for_the_loser(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        document = await uow.documents.create(
            ctx, title="Original", folder_id=None, content=version_content("race")
        )

        # Both readers hold the same version number, as two browser tabs would.
        stale_version = document.version

        await uow.documents.update(
            ctx, document.id, edit=DocumentEdit(title="Winner"), expected_version=stale_version
        )
        with pytest.raises(ConflictError):
            await uow.documents.update(
                ctx, document.id, edit=DocumentEdit(title="Loser"), expected_version=stale_version
            )

        final = await uow.documents.get(ctx, document.id)
        assert final is not None
        assert final.title == "Winner", "the first write must survive"


class TestKeysetPagination:
    @pytest.fixture
    async def many_documents(self, uow: UnitOfWork, ctx: AccessContext) -> list[Document]:
        created = []
        for index in range(12):
            created.append(
                await uow.documents.create(
                    ctx,
                    title=f"Document {index:02d}",
                    folder_id=None,
                    content=version_content(f"page-{index}"),
                )
            )
        await uow.flush()
        return created

    async def test_returns_a_bounded_page(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        page = await uow.documents.list_page(ctx, limit=5)
        assert len(page.items) == 5
        assert page.has_more

    async def test_pages_cover_every_row_exactly_once(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        """The property offset pagination cannot provide: no duplicates, no gaps."""
        seen: list[uuid.UUID] = []
        cursor: str | None = None
        for _ in range(10):
            page = await uow.documents.list_page(ctx, limit=5, cursor=cursor)
            seen.extend(d.id for d in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(many_documents)
        assert len(set(seen)) == len(seen), "a document appeared on two pages"
        assert set(seen) == {d.id for d in many_documents}

    async def test_ordering_is_newest_first(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        page = await uow.documents.list_page(ctx, limit=12)
        titles = [d.title for d in page.items]
        assert titles == sorted(titles, reverse=True)

    async def test_the_last_page_has_no_cursor(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        page = await uow.documents.list_page(ctx, limit=50)
        assert page.next_cursor is None
        assert not page.has_more

    async def test_a_tampered_cursor_is_rejected(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        """Cursors are signed: an unsigned one could be crafted to page outside
        the filter it was issued for."""
        page = await uow.documents.list_page(ctx, limit=5)
        assert page.next_cursor is not None
        tampered = page.next_cursor[:-4] + "AAAA"

        with pytest.raises(BadRequestError):
            await uow.documents.list_page(ctx, limit=5, cursor=tampered)

    async def test_filters_by_status(
        self, uow: UnitOfWork, ctx: AccessContext, many_documents: list[Document]
    ) -> None:
        target = many_documents[0]
        assert target.current_version is not None
        await uow.documents.set_version_status(
            ctx, target.current_version.id, outcome=ProcessingOutcome.ready(chunk_count=3)
        )
        await uow.flush()

        page = await uow.documents.list_page(
            ctx, limit=50, query=DocumentListQuery(status=ProcessingStatus.READY)
        )
        assert [d.id for d in page.items] == [target.id]


class TestQueryPlans:
    """An index that exists is not an index that is used."""

    async def _plan(self, uow: UnitOfWork, sql: str, params: dict[str, object]) -> str:
        # ANALYZE would need representative data volumes; the plan shape is what
        # matters here, and it is stable at any size once enable_seqscan is off.
        result = await uow.session.execute(text(f"EXPLAIN {sql}"), params)
        return "\n".join(row[0] for row in result)

    async def test_document_list_uses_the_keyset_index(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """The `(workspace_id, created_at DESC, id DESC)` index only helps if the
        query's ORDER BY matches it exactly."""
        await uow.flush()
        # Force the choice: on an empty table a sequential scan is genuinely
        # cheaper, so this asserts the index is *usable*, not that it wins today.
        await uow.session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await self._plan(
            uow,
            "SELECT * FROM documents WHERE workspace_id = :w AND deleted_at IS NULL"
            " ORDER BY created_at DESC, id DESC LIMIT 25",
            {"w": ctx.workspace_id},
        )
        assert "ix_documents_workspace_id_created_at_id" in plan, plan

    async def test_email_lookup_uses_the_functional_index(self, uow: UnitOfWork) -> None:
        """Comparing `lower(email)` is what makes the functional index usable;
        comparing the raw column would scan on every login."""
        await uow.flush()
        await uow.session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await self._plan(
            uow,
            "SELECT * FROM users WHERE lower(email) = :e AND deleted_at IS NULL",
            {"e": "someone@example.test"},
        )
        assert "uq_users_email_lower" in plan, plan

    async def test_status_poll_uses_the_partial_index(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await uow.flush()
        await uow.session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await self._plan(
            uow,
            "SELECT * FROM document_versions WHERE workspace_id = :w"
            " AND status IN ('pending', 'processing')",
            {"w": ctx.workspace_id},
        )
        assert "ix_document_versions_workspace_id_status" in plan, plan

    async def test_membership_resolution_uses_its_index(
        self, uow: UnitOfWork, user: User, ctx: AccessContext
    ) -> None:
        """Issued on every request that builds an AccessContext, so it is the
        hottest lookup in the system."""
        await uow.flush()
        await uow.session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await self._plan(
            uow,
            "SELECT * FROM workspace_members WHERE user_id = :u",
            {"u": user.id},
        )
        assert "workspace_members" in plan
        assert "Seq Scan" not in plan, plan
