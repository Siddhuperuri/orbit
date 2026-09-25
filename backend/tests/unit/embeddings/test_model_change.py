"""What happens when the embedding model changes, and dense search, on fakes.

The scenario: a corpus indexed under model v1, then `ORBIT_EMBEDDING_MODEL`
changes to v2 at the same width. Vectors from the two are not comparable, so
until re-indexed the v1 chunks must be *excluded* from dense search -- never
ranked against a v2 query vector -- and the re-index must move them without
disturbing anything a user or a citation can see.
"""

from __future__ import annotations

import dataclasses

import pytest

from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.embeddings.reindex import GetIndexCoverage, ReindexEmbeddings
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConfigurationError, ValidationError
from orbit.domain.models.entities import Document, ProcessingStatus
from orbit.domain.retrieval import (
    MAX_QUERY_CHARACTERS,
    RetrievalMode,
    SearchQuery,
    SearchResult,
)
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from tests.unit.fakes.fake_processing import StoredChunk
from tests.unit.processing.harness import ControllableEmbedder, Pipeline, build_pipeline

LEAVE = (
    "Parental leave is sixteen weeks at full pay. Leave can start up to four weeks "
    "before the expected birth and must be booked through the leave portal. "
) * 4
INCIDENT = (
    "Security incidents are reported to the on-call engineer within one hour. The "
    "incident commander opens a bridge and records a timeline for the postmortem. "
) * 4
EXPENSES = (
    "Expense claims need an itemised receipt. Claims above five hundred dollars need "
    "approval from a budget holder before reimbursement is processed. "
) * 4


def _model(version: int, *, dimensions: int = 32) -> ControllableEmbedder:
    return ControllableEmbedder(
        inner=FakeEmbeddingProvider(
            dimensions=dimensions, model_id=f"orbit-fake-embedding-v{version}"
        )
    )


async def _indexed_corpus() -> tuple[Pipeline, dict[str, Document]]:
    pipeline = await build_pipeline(embedder=_model(1))
    documents = {
        "leave": await pipeline.upload("leave.txt", LEAVE.encode()),
        "incident": await pipeline.upload("incident.txt", INCIDENT.encode()),
        "expenses": await pipeline.upload("expenses.txt", EXPENSES.encode()),
    }
    await pipeline.drain()
    assert all(
        pipeline.version(document).status is ProcessingStatus.READY
        for document in documents.values()
    )
    return pipeline, documents


def _all_chunks(pipeline: Pipeline) -> list[StoredChunk]:
    return sorted(
        (chunk for rows in pipeline.uow_factory.state.chunks.values() for chunk in rows),
        key=lambda chunk: chunk.chunk_id,
    )


class _SemanticSearch:
    """Dense retrieval alone, through the production use case."""

    def __init__(self, pipeline: Pipeline, embedder: ControllableEmbedder) -> None:
        self._search = HybridSearch(pipeline.uow_factory, embedder, SearchPolicy(ef_search=40))

    async def execute(
        self, ctx: AccessContext, text: str, *, limit: int = 10
    ) -> list[SearchResult]:
        response = await self._search.execute(
            ctx, SearchQuery(text=text, limit=limit, mode=RetrievalMode.SEMANTIC)
        )
        return list(response.results)


def _search(pipeline: Pipeline, embedder: ControllableEmbedder) -> _SemanticSearch:
    return _SemanticSearch(pipeline, embedder)


class TestAfterAModelChange:
    async def test_coverage_reports_every_live_chunk_as_stale(self) -> None:
        pipeline, _ = await _indexed_corpus()
        total = len(_all_chunks(pipeline))

        coverage = await GetIndexCoverage(pipeline.uow_factory, _model(2)).execute()

        assert (coverage.indexed_chunks, coverage.stale_chunks) == (0, total)
        assert coverage.schema_matches and not coverage.is_complete
        (usage,) = coverage.spaces
        assert (usage.space_key, usage.chunks, usage.versions) == (
            "orbit-fake-embedding-v1@32",
            total,
            3,
        )

    async def test_dense_search_excludes_vectors_from_another_space_rather_than_ranking_them(
        self,
    ) -> None:
        pipeline, documents = await _indexed_corpus()

        old = await _search(pipeline, _model(1)).execute(pipeline.ctx, "parental leave weeks")
        new = await _search(pipeline, _model(2)).execute(pipeline.ctx, "parental leave weeks")

        assert old and old[0].document.id == documents["leave"].id
        assert new == [], "a v2 query vector is never compared with v1 vectors"


class TestReindex:
    async def test_moves_every_chunk_into_the_active_space_without_disturbing_anything(
        self,
    ) -> None:
        pipeline, documents = await _indexed_corpus()
        before = _all_chunks(pipeline)
        versions_before = {d: pipeline.version(doc) for d, doc in documents.items()}
        v2 = _model(2)

        report = await ReindexEmbeddings(
            pipeline.uow_factory, v2, pipeline.clock, batch_size=2
        ).execute()

        after = _all_chunks(pipeline)
        assert report.complete
        assert (report.scanned, report.updated, report.superseded, report.inconsistent) == (
            len(before),
            len(before),
            0,
            0,
        )
        assert {chunk.space for chunk in after} == {v2.space}
        # Identity, text, and provenance are untouched -- citations still resolve.
        assert [(c.chunk_id, c.text, c.ordinal, c.version_id) for c in after] == [
            (c.chunk_id, c.text, c.ordinal, c.version_id) for c in before
        ]
        for chunk in after:
            assert list(chunk.embedding) == list(v2.inner.vector(_input(chunk)))
        # The documents never left READY.
        assert {d: pipeline.version(doc) for d, doc in documents.items()} == versions_before

        coverage = await GetIndexCoverage(pipeline.uow_factory, v2).execute()
        assert coverage.is_complete
        hits = await _search(pipeline, v2).execute(pipeline.ctx, "security incident on-call")
        assert hits[0].document.id == documents["incident"].id

    async def test_is_idempotent(self) -> None:
        pipeline, _ = await _indexed_corpus()
        v2 = _model(2)
        reindex = ReindexEmbeddings(pipeline.uow_factory, v2, pipeline.clock, batch_size=50)
        await reindex.execute()
        calls = v2.calls

        again = await reindex.execute()

        assert (again.complete, again.scanned, again.updated) == (True, 0, 0)
        assert v2.calls == calls, "a finished re-index costs nothing to re-run"

    async def test_is_resumable_after_an_interruption(self) -> None:
        pipeline, _ = await _indexed_corpus()
        total = len(_all_chunks(pipeline))
        v2 = _model(2)
        reindex = ReindexEmbeddings(pipeline.uow_factory, v2, pipeline.clock, batch_size=50)

        partial = await reindex.execute(max_chunks=1)
        rest = await reindex.execute()

        assert (partial.complete, partial.updated) == (False, 1)
        assert (rest.complete, rest.updated) == (True, total - 1)

    async def test_a_provider_failure_stops_the_run_and_loses_no_committed_progress(
        self,
    ) -> None:
        pipeline, _ = await _indexed_corpus()
        total = len(_all_chunks(pipeline))
        v2 = _model(2)
        v2.batch_size = 1
        reindex = ReindexEmbeddings(pipeline.uow_factory, v2, pipeline.clock, batch_size=1)

        async def outage() -> None:
            v2.failures.append(ConnectionError("provider down"))

        v2.before_return = outage
        with pytest.raises(ConnectionError):
            await reindex.execute()

        moved = [c for c in _all_chunks(pipeline) if c.space == v2.space]
        assert len(moved) == 1
        finished = await reindex.execute()
        assert (finished.complete, finished.updated) == (True, total - 1)

    async def test_a_chunk_rebuilt_during_the_run_is_left_to_its_rebuild(self) -> None:
        pipeline, documents = await _indexed_corpus()
        v2 = _model(2)
        state = pipeline.uow_factory.state
        target = pipeline.version(documents["leave"])

        async def concurrent_reprocess() -> None:
            # The chunk's input changes between being read and being updated.
            state.chunks[target.id] = [
                dataclasses.replace(chunk, embedding_input_sha256="0" * 64, text="rewritten")
                for chunk in state.chunks[target.id]
            ]

        v2.before_return = concurrent_reprocess
        report = await ReindexEmbeddings(
            pipeline.uow_factory, v2, pipeline.clock, batch_size=100
        ).execute()

        rebuilt = state.chunks[target.id]
        assert report.superseded == len(rebuilt)
        assert all(chunk.space != v2.space for chunk in rebuilt)

    async def test_a_chunk_whose_text_disagrees_with_its_hash_is_reported_not_embedded(
        self,
    ) -> None:
        pipeline, documents = await _indexed_corpus()
        state = pipeline.uow_factory.state
        target = pipeline.version(documents["expenses"])
        corrupted, *rest = state.chunks[target.id]
        state.chunks[target.id] = [dataclasses.replace(corrupted, text="tampered"), *rest]
        v2 = _model(2)

        report = await ReindexEmbeddings(
            pipeline.uow_factory, v2, pipeline.clock, batch_size=100
        ).execute()

        assert report.inconsistent == 1
        assert "tampered" not in v2.inputs
        assert state.chunks[target.id][0].space != v2.space

    async def test_deleted_documents_are_not_paid_for(self) -> None:
        pipeline, documents = await _indexed_corpus()
        deleted = pipeline.version(documents["incident"])
        await DeleteDocument(pipeline.uow_factory).execute(pipeline.ctx, documents["incident"].id)
        live = sum(
            len(rows)
            for vid, rows in pipeline.uow_factory.state.chunks.items()
            if vid != deleted.id
        )
        v2 = _model(2)

        report = await ReindexEmbeddings(
            pipeline.uow_factory, v2, pipeline.clock, batch_size=100
        ).execute()

        assert report.updated == live
        assert not any("incident" in text.lower() for text in v2.inputs)

    async def test_a_change_of_width_is_refused_as_a_migration_not_attempted(self) -> None:
        pipeline, _ = await _indexed_corpus()
        wider = _model(2, dimensions=64)

        with pytest.raises(ConfigurationError, match="migration"):
            await ReindexEmbeddings(
                pipeline.uow_factory, wider, pipeline.clock, batch_size=100
            ).execute()
        assert wider.calls == 0


def _input(chunk: StoredChunk) -> str:
    return f"{chunk.heading_path}\n\n{chunk.text}" if chunk.heading_path else chunk.text


class TestDenseSearch:
    async def test_nearest_passages_come_first(self) -> None:
        pipeline, documents = await _indexed_corpus()
        search = _search(pipeline, pipeline.embedder)

        for query, expected in [
            ("how many weeks of parental leave", "leave"),
            ("who do I report a security incident to", "incident"),
            ("receipt needed for an expense claim", "expenses"),
        ]:
            hits = await search.execute(pipeline.ctx, query, limit=3)
            assert hits[0].document.id == documents[expected].id, query
            similarities = [hit.relevance.semantic.score for hit in hits if hit.relevance.semantic]
            assert similarities == sorted(similarities, reverse=True)

    async def test_another_workspace_sees_nothing(self) -> None:
        pipeline, _ = await _indexed_corpus()
        workspace = await CreateWorkspace(pipeline.uow_factory).execute(
            name="Globex", created_by_user_id=pipeline.ctx.user_id
        )
        outsider = AccessContext(
            user_id=pipeline.ctx.user_id, workspace_id=workspace.id, role=Role.VIEWER
        )
        assert await _search(pipeline, pipeline.embedder).execute(outsider, "parental leave") == []

    async def test_a_deleted_document_is_not_returned(self) -> None:
        pipeline, documents = await _indexed_corpus()
        await DeleteDocument(pipeline.uow_factory).execute(pipeline.ctx, documents["leave"].id)
        hits = await _search(pipeline, pipeline.embedder).execute(
            pipeline.ctx, "parental leave", limit=10
        )
        assert documents["leave"].id not in {hit.document.id for hit in hits}

    @pytest.mark.parametrize(
        ("query", "limit"),
        [("   ", 10), ("x" * (MAX_QUERY_CHARACTERS + 1), 10), ("ok", 0), ("ok", 51)],
        ids=["blank", "too-long", "limit-zero", "limit-too-high"],
    )
    async def test_invalid_requests_are_refused_before_paying_the_provider(
        self, query: str, limit: int
    ) -> None:
        pipeline, _ = await _indexed_corpus()
        embedder = _model(1)
        with pytest.raises(ValidationError):
            await _search(pipeline, embedder).execute(pipeline.ctx, query, limit=limit)
        assert embedder.inputs == []
