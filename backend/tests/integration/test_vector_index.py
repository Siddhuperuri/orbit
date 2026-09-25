"""The vector index against real PostgreSQL + pgvector.

What only the real database can prove: the schema refuses an unembedded or
mislabelled chunk, vectors round-trip bit-exactly, reuse and re-embedding are
scoped as designed, dense search returns exactly the brute-force nearest
neighbours with every visibility rule applied inside the query, the query is
one the HNSW index can serve, and the readiness probe catches a width
mismatch.
"""

from __future__ import annotations

import uuid
from array import array
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from orbit.core.config import Settings
from orbit.domain.access import AccessContext, SystemContext
from orbit.domain.embeddings import EmbeddingSpace, embedding_input_sha256
from orbit.domain.errors import ConfigurationError
from orbit.domain.models.entities import (
    Document,
    DocumentVersion,
    ProcessingOutcome,
    ProcessingStatus,
)
from orbit.domain.ports.embeddings import EmbeddingUpdate
from orbit.domain.processing.content import ChunkDraft, EmbeddedChunk
from orbit.domain.retrieval import SearchFilters
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from orbit.infrastructure.health import EmbeddingSchemaProbe
from tests.integration.conftest import version_content

pytestmark = pytest.mark.integration

SYSTEM = SystemContext(reason="integration-test")
DIMENSIONS = 1536
V1 = EmbeddingSpace(model="orbit-fake-embedding-v1", dimensions=DIMENSIONS)
V2 = EmbeddingSpace(model="orbit-fake-embedding-v2", dimensions=DIMENSIONS)
EMBEDDED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
CHUNKER = "sa1-512-768-64-32"
ALL = SearchFilters()


def _unit_vectors(rng: np.random.Generator, count: int) -> np.ndarray:
    vectors = rng.standard_normal((count, DIMENSIONS)).astype(np.float32)
    normalized: np.ndarray = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    return normalized


def _as_array(vector: np.ndarray) -> array[float]:
    compact = array("f")
    compact.frombytes(vector.astype(np.float32).tobytes())
    return compact


async def _indexed_document(  # noqa: PLR0913 -- a test factory with defaults
    uow: UnitOfWork,
    ctx: AccessContext,
    marker: str,
    vectors: Sequence[array[float]],
    *,
    space: EmbeddingSpace = V1,
    heading: tuple[str, ...] = (),
    ready: bool = True,
) -> tuple[Document, DocumentVersion]:
    document = await uow.documents.create(
        ctx, title=marker, folder_id=None, content=version_content(f"{marker}-{uuid.uuid4()}")
    )
    version = document.current_version
    assert version is not None
    chunks = []
    for ordinal, vector in enumerate(vectors):
        body = f"{marker} passage {ordinal}"
        draft = ChunkDraft(
            ordinal=ordinal,
            text=body,
            token_count=4,
            char_start=ordinal * 100,
            char_end=ordinal * 100 + len(body),
            page_start=None,
            page_end=None,
            heading_path=heading,
        )
        chunks.append(EmbeddedChunk(draft=draft, embedding=vector, embedded_at=EMBEDDED_AT))
    await uow.processing.replace_chunks(
        SYSTEM, version, chunks, space=space, chunker_version=CHUNKER
    )
    if ready:
        moved = await uow.processing.transition_version(
            SYSTEM,
            version.id,
            expected=frozenset({ProcessingStatus.PENDING}),
            outcome=ProcessingOutcome.ready(chunk_count=len(chunks)),
        )
        assert moved
    return document, version


async def _expect_rejected(uow: UnitOfWork, sql: str, **params: object) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        async with uow.session.begin_nested():
            await uow.session.execute(text(sql), params)


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


class TestSchemaGuarantees:
    async def test_the_catalog_reports_the_column_width(self, uow: UnitOfWork) -> None:
        assert await uow.embeddings.column_dimensions(SYSTEM) == DIMENSIONS

    async def test_a_chunk_is_stored_with_exact_metadata_and_a_bit_exact_vector(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(1)
        (vector,) = _unit_vectors(rng, 1)
        _, version = await _indexed_document(
            uow, ctx, "meta", [_as_array(vector)], heading=("Security", "Tokens")
        )

        (row,) = (
            await uow.session.execute(
                text(
                    "SELECT embedding_model, embedding_dimensions, embedding_input_sha256,"
                    " embedded_at, vector_dims(embedding) AS dims, heading_path, content,"
                    " document_version_id, workspace_id"
                    " FROM chunks WHERE document_version_id = :v"
                ),
                {"v": version.id},
            )
        ).mappings()
        assert (row["embedding_model"], row["embedding_dimensions"], row["dims"]) == (
            V1.model,
            DIMENSIONS,
            DIMENSIONS,
        )
        assert row["embedding_input_sha256"] == embedding_input_sha256(
            row["heading_path"], row["content"]
        )
        assert row["embedded_at"] == EMBEDDED_AT
        assert (row["document_version_id"], row["workspace_id"]) == (version.id, ctx.workspace_id)

        stored = await uow.embeddings.find_reusable(
            SYSTEM,
            workspace_id=ctx.workspace_id,
            input_hashes=[row["embedding_input_sha256"]],
            space=V1,
        )
        assert list(stored[row["embedding_input_sha256"]].vector) == list(_as_array(vector))

    async def test_a_chunk_without_a_vector_is_unrepresentable(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(2)
        _, version = await _indexed_document(uow, ctx, "nn", [_as_array(_unit_vectors(rng, 1)[0])])
        for column in (
            "embedding",
            "embedding_model",
            "embedding_dimensions",
            "embedding_input_sha256",
            "embedded_at",
        ):
            await _expect_rejected(
                uow,
                f"UPDATE chunks SET {column} = NULL WHERE document_version_id = :v",  # noqa: S608
                v=version.id,
            )

    async def test_recorded_width_must_be_the_vectors_width(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(3)
        _, version = await _indexed_document(uow, ctx, "w", [_as_array(_unit_vectors(rng, 1)[0])])
        await _expect_rejected(
            uow,
            "UPDATE chunks SET embedding_dimensions = 768 WHERE document_version_id = :v",
            v=version.id,
        )

    async def test_a_vector_of_another_width_cannot_be_stored(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(4)
        _, version = await _indexed_document(uow, ctx, "x", [_as_array(_unit_vectors(rng, 1)[0])])
        await _expect_rejected(
            uow,
            "UPDATE chunks SET embedding = '[1,0,0]'::vector, embedding_dimensions = 3"
            " WHERE document_version_id = :v",
            v=version.id,
        )

    async def test_the_index_transaction_verifies_what_it_wrote(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(5)
        vectors = [_as_array(v) for v in _unit_vectors(rng, 3)]
        _, version = await _indexed_document(uow, ctx, "count", vectors, ready=False)
        assert await uow.embeddings.count_indexed(SYSTEM, version.id, space=V1) == 3
        assert await uow.embeddings.count_indexed(SYSTEM, version.id, space=V2) == 0


# ---------------------------------------------------------------------------
# Reuse, coverage, and re-embedding
# ---------------------------------------------------------------------------


class TestIndexRepository:
    async def test_reuse_is_scoped_to_the_workspace_and_the_space(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        rng = np.random.default_rng(6)
        vectors = [_as_array(v) for v in _unit_vectors(rng, 2)]
        await _indexed_document(uow, ctx, "shared", vectors[:1])
        await _indexed_document(uow, ctx, "shared-v2", vectors[1:], space=V2)
        wanted = [embedding_input_sha256(None, "shared passage 0")]

        assert set(
            await uow.embeddings.find_reusable(
                SYSTEM, workspace_id=ctx.workspace_id, input_hashes=wanted, space=V1
            )
        ) == set(wanted)
        assert not await uow.embeddings.find_reusable(
            SYSTEM, workspace_id=other_ctx.workspace_id, input_hashes=wanted, space=V1
        )
        assert not await uow.embeddings.find_reusable(
            SYSTEM, workspace_id=ctx.workspace_id, input_hashes=wanted, space=V2
        )

    async def test_coverage_counts_only_live_chunks_per_space(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        before = await uow.embeddings.coverage(SYSTEM, space=V2)
        rng = np.random.default_rng(7)
        vectors = [_as_array(v) for v in _unit_vectors(rng, 6)]
        await _indexed_document(uow, ctx, "cov-a", vectors[:2])
        await _indexed_document(uow, ctx, "cov-b", vectors[2:3], space=V2)
        deleted, _ = await _indexed_document(uow, ctx, "cov-deleted", vectors[3:6])
        await uow.documents.soft_delete(ctx, deleted.id)

        after = await uow.embeddings.coverage(SYSTEM, space=V2)

        assert after.column_dimensions == DIMENSIONS and after.schema_matches
        assert after.indexed_chunks - before.indexed_chunks == 1
        assert after.stale_chunks - before.stale_chunks == 2
        assert after.chunker_versions[CHUNKER] - before.chunker_versions.get(CHUNKER, 0) == 3

    async def test_re_embedding_in_place_is_conditional_and_idempotent(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(8)
        old, new = (_as_array(v) for v in _unit_vectors(rng, 2))
        _, version = await _indexed_document(uow, ctx, "reembed", [old])
        stale = [
            chunk
            for chunk in await uow.embeddings.find_stale(SYSTEM, space=V2, after=None, limit=10_000)
            if chunk.workspace_id == ctx.workspace_id
        ]
        (chunk,) = stale
        moved_at = datetime(2026, 9, 17, tzinfo=UTC)

        wrong_input = EmbeddingUpdate(chunk.chunk_id, "f" * 64, new, moved_at)
        assert await uow.embeddings.update_embeddings(SYSTEM, [wrong_input], space=V2) == 0

        right = EmbeddingUpdate(chunk.chunk_id, chunk.embedding_input_sha256, new, moved_at)
        assert await uow.embeddings.update_embeddings(SYSTEM, [right], space=V2) == 1
        assert await uow.embeddings.update_embeddings(SYSTEM, [right], space=V2) == 0

        (row,) = (
            await uow.session.execute(
                text(
                    "SELECT id, embedding_model, embedded_at FROM chunks"
                    " WHERE document_version_id = :v"
                ),
                {"v": version.id},
            )
        ).all()
        assert (row.id, row.embedding_model, row.embedded_at) == (
            chunk.chunk_id,
            V2.model,
            moved_at,
        )
        stored = await uow.embeddings.find_reusable(
            SYSTEM,
            workspace_id=ctx.workspace_id,
            input_hashes=[chunk.embedding_input_sha256],
            space=V2,
        )
        assert list(stored[chunk.embedding_input_sha256].vector) == list(new)


# ---------------------------------------------------------------------------
# Dense search
# ---------------------------------------------------------------------------


class TestDenseSearch:
    async def test_returns_exactly_the_brute_force_nearest_neighbours(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        rng = np.random.default_rng(42)
        mine = _unit_vectors(rng, 240)
        theirs = _unit_vectors(rng, 120)
        ids: list[uuid.UUID] = []
        for start in range(0, len(mine), 24):
            _, version = await _indexed_document(
                uow, ctx, f"mine-{start}", [_as_array(v) for v in mine[start : start + 24]]
            )
            ids.extend(
                (
                    await uow.session.execute(
                        text(
                            "SELECT id FROM chunks WHERE document_version_id = :v ORDER BY ordinal"
                        ),
                        {"v": version.id},
                    )
                ).scalars()
            )
        await _indexed_document(uow, other_ctx, "theirs", [_as_array(v) for v in theirs])
        await uow.session.execute(text("ANALYZE chunks"))

        for query in _unit_vectors(rng, 12):
            hits = await uow.search.semantic_candidates(
                ctx, _as_array(query), space=V1, filters=ALL, limit=10, ef_search=200
            )
            similarities = mine @ query
            expected = [ids[i] for i in np.argsort(-similarities)[:10]]
            assert [hit.chunk_id for hit in hits] == expected
            for hit, index in zip(hits, np.argsort(-similarities)[:10], strict=True):
                assert hit.score == pytest.approx(float(similarities[index]), abs=1e-5)

    async def test_only_searchable_chunks_in_the_callers_workspace_and_space_are_returned(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        rng = np.random.default_rng(9)
        (target,) = _unit_vectors(rng, 1)
        same = _as_array(target)
        visible, _ = await _indexed_document(uow, ctx, "visible", [same])
        await _indexed_document(uow, other_ctx, "other-tenant", [same])
        await _indexed_document(uow, ctx, "other-space", [same], space=V2)
        await _indexed_document(uow, ctx, "not-ready", [same], ready=False)
        deleted, _ = await _indexed_document(uow, ctx, "deleted", [same])
        await uow.documents.soft_delete(ctx, deleted.id)
        _, old_version = await _indexed_document(uow, ctx, "superseded", [same])
        # Demote without the swap's own chunk deletion, so the query's
        # `is_current` predicate -- not the swap -- is what is under test.
        await uow.session.execute(
            text("UPDATE document_versions SET is_current = false WHERE id = :v"),
            {"v": old_version.id},
        )

        hits = await uow.search.semantic_candidates(
            ctx, same, space=V1, filters=ALL, limit=50, ef_search=100
        )
        records = await uow.search.load_results(ctx, [h.chunk_id for h in hits], filters=ALL)

        assert {record.document_id for record in records.values()} == {visible.id}
        assert len(hits) == 1
        assert hits[0].score == pytest.approx(1.0, abs=1e-6)

    async def test_the_query_can_be_served_by_the_hnsw_index(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rng = np.random.default_rng(10)
        await _indexed_document(uow, ctx, "plan", [_as_array(v) for v in _unit_vectors(rng, 20)])
        captured: list[tuple[str, object]] = []
        sync_connection = (await uow.session.connection()).sync_connection
        assert sync_connection is not None

        def capture(_c: object, _cur: object, statement: str, params: object, *_: object) -> None:
            if "<=>" in statement:
                captured.append((statement, params))

        event.listen(sync_connection, "before_cursor_execute", capture)
        try:
            await uow.search.semantic_candidates(
                ctx,
                _as_array(_unit_vectors(rng, 1)[0]),
                space=V1,
                filters=ALL,
                limit=10,
                ef_search=40,
            )
        finally:
            event.remove(sync_connection, "before_cursor_execute", capture)
        (statement, params) = captured[0]

        # As in test_transactions_and_queries: on a tiny table an exact scan
        # is genuinely cheaper, so the alternatives are disabled to assert the
        # index is *usable* by this exact statement -- the label ordering, the
        # CTE, and the joins included.
        for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            await uow.session.execute(text(f"SET LOCAL {setting} = off"))
        raw = await uow.session.connection()
        plan = await raw.exec_driver_sql(f"EXPLAIN {statement}", params)  # type: ignore[arg-type]
        text_plan = "\n".join(row[0] for row in plan)
        assert "ix_chunks_embedding_hnsw" in text_plan, text_plan


# ---------------------------------------------------------------------------
# Migration 0004's backfill
# ---------------------------------------------------------------------------


async def _migrate(engine: AsyncEngine, revision: str) -> None:
    def go(connection: object) -> None:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        if revision == "0003_processing_pipeline":
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)

    async with engine.begin() as conn:
        await conn.run_sync(go)


class TestZzMigrationBackfill:
    """Named to run last in the module: it takes the shared schema down one
    revision and back to head."""

    async def test_existing_chunks_are_backfilled_exactly_and_unembedded_ones_refused(
        self, migrated_engine: AsyncEngine
    ) -> None:
        await _migrate(migrated_engine, "0003_processing_pipeline")
        try:
            async with AsyncSession(bind=migrated_engine, expire_on_commit=False) as session:
                uow = UnitOfWork(session, cursor_secret="backfill")
                user = await uow.users.create(
                    email=f"backfill-{uuid.uuid4().hex[:8]}@example.test",
                    password_hash="$argon2id$fake",
                    full_name="Backfill",
                )
                workspace = await uow.workspaces.create(
                    name="Backfill",
                    slug=f"backfill-{uuid.uuid4().hex[:8]}",
                    created_by_user_id=user.id,
                )
                await uow.memberships.add_owner(workspace.id, user.id)
                await uow.commit()

            # Raw SQL, not `uow.documents.create`: the schema is deliberately at an
            # *old* revision here, and the ORM describes the current one. Seeding
            # through it would break this test every time a column is added to
            # `documents` -- which is precisely what it has no business caring about.
            stored_file = version_content("backfill")
            document_id, version_id = uuid.uuid4(), uuid.uuid4()
            async with migrated_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO documents (id, workspace_id, title, version)"
                        " VALUES (:d, :w, 'Backfill', 1)"
                    ),
                    {"d": document_id, "w": workspace.id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO document_versions (id, document_id, workspace_id,"
                        " version_number, is_current, storage_key, content_sha256, byte_size,"
                        " content_type, original_filename) VALUES (:v, :d, :w, 1, true, :k, :h,"
                        " :b, :t, :f)"
                    ),
                    {
                        "v": version_id,
                        "d": document_id,
                        "w": workspace.id,
                        "k": stored_file.storage_key,
                        "h": stored_file.content_sha256,
                        "b": stored_file.byte_size,
                        "t": stored_file.content_type,
                        "f": stored_file.original_filename,
                    },
                )
            rows = [
                ("Security > Tokens", "Rotate refresh tokens on every use.", True),
                (None, "Plain text with no heading.", True),
                ("", "An empty heading path embeds the text alone.", True),
                (None, "Never embedded.", False),
            ]
            async with migrated_engine.begin() as conn:
                for ordinal, (heading, content, embedded) in enumerate(rows):
                    await conn.execute(
                        text(
                            "INSERT INTO chunks (id, workspace_id, document_id,"
                            " document_version_id, ordinal, content, token_count, char_start,"
                            " char_end, heading_path, content_sha256, embedding, embedding_model,"
                            " chunker_version) VALUES (:id, :w, :d, :v, :o, :c, 5, :s, :e, :h,"
                            " :sha, CAST(:vec AS vector), :m, :cv)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "w": workspace.id,
                            "d": document_id,
                            "v": version_id,
                            "o": ordinal,
                            "c": content,
                            "s": ordinal * 100,
                            "e": ordinal * 100 + len(content),
                            "h": heading,
                            "sha": "a" * 64,
                            "vec": "[" + ",".join(["0.5"] * 4 + ["0"] * (DIMENSIONS - 4)) + "]"
                            if embedded
                            else None,
                            "m": V1.model if embedded else None,
                            "cv": CHUNKER,
                        },
                    )

            with pytest.raises(RuntimeError, match="without an embedding"):
                await _migrate(migrated_engine, "head")

            async with migrated_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM chunks WHERE document_version_id = :v AND embedding IS NULL"),
                    {"v": version_id},
                )
            await _migrate(migrated_engine, "head")

            async with migrated_engine.connect() as conn:
                stored = (
                    await conn.execute(
                        text(
                            "SELECT heading_path, content, embedding_input_sha256,"
                            " embedding_dimensions, embedded_at FROM chunks"
                            " WHERE document_version_id = :v ORDER BY ordinal"
                        ),
                        {"v": version_id},
                    )
                ).all()
            assert len(stored) == 3
            for row in stored:
                assert row.embedding_input_sha256 == embedding_input_sha256(
                    row.heading_path, row.content
                ), "SQL backfill and build_embedding_input must agree byte for byte"
                assert row.embedding_dimensions == DIMENSIONS
                assert row.embedded_at is not None
        finally:
            await _migrate(migrated_engine, "head")
            async with migrated_engine.begin() as conn:
                await conn.execute(text("DELETE FROM workspaces WHERE slug LIKE 'backfill-%'"))
                await conn.execute(text("DELETE FROM users WHERE email LIKE 'backfill-%'"))


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


class TestEmbeddingSchemaProbe:
    async def test_passes_when_configuration_matches_the_column(
        self, integration_settings: Settings, migrated_engine: AsyncEngine
    ) -> None:
        del migrated_engine
        database = Database(integration_settings)
        try:
            await EmbeddingSchemaProbe(database, expected_dimensions=DIMENSIONS).check()
        finally:
            await database.dispose()

    async def test_refuses_readiness_when_the_width_disagrees(
        self, integration_settings: Settings, migrated_engine: AsyncEngine
    ) -> None:
        del migrated_engine
        database = Database(integration_settings)
        try:
            with pytest.raises(ConfigurationError):
                await EmbeddingSchemaProbe(database, expected_dimensions=768).check()
        finally:
            await database.dispose()
