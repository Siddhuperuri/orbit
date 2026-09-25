"""Hybrid search against real PostgreSQL full-text search and pgvector.

Everything here runs the production SQL. The unit suite proves fusion and the
use case's rules; this proves the parts only PostgreSQL decides: stemming,
stop words, `ts_rank_cd`, the OR rewrite, the GIN plan, and -- above all --
that the workspace predicate inside both retrievers keeps another tenant's
chunk out even when it is the exact match.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, text

from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.domain.access import AccessContext, SystemContext
from orbit.domain.models.entities import Document, ProcessingOutcome, ProcessingStatus
from orbit.domain.processing.content import ChunkDraft, EmbeddedChunk
from orbit.domain.retrieval import (
    LexicalMatch,
    MatchedBy,
    RetrievalMethod,
    RetrievalMode,
    SearchFilters,
    SearchQuery,
)
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.db.models import Chunk as ChunkRow
from orbit.infrastructure.db.repositories.search import ts_query
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import version_content

pytestmark = pytest.mark.integration

SYSTEM = SystemContext(reason="integration-test")
PROVIDER = FakeEmbeddingProvider(dimensions=1536)
SPACE = PROVIDER.space
ALL = SearchFilters()


async def _document(
    uow: UnitOfWork,
    ctx: AccessContext,
    title: str,
    passages: Sequence[str],
    *,
    ready: bool = True,
) -> Document:
    document = await uow.documents.create(
        ctx, title=title, folder_id=None, content=version_content(f"{title}-{uuid.uuid4()}")
    )
    version = document.current_version
    assert version is not None
    chunks, offset = [], 0
    for ordinal, passage in enumerate(passages):
        draft = ChunkDraft(
            ordinal=ordinal,
            text=passage,
            token_count=max(1, len(passage.split())),
            char_start=offset,
            char_end=offset + len(passage),
            page_start=ordinal + 1,
            page_end=ordinal + 1,
            heading_path=(title,),
        )
        offset += len(passage) + 2
        chunks.append(
            EmbeddedChunk(
                draft=draft,
                embedding=PROVIDER.vector(draft.embedding_input),
                embedded_at=datetime.now(UTC),
            )
        )
    await uow.processing.replace_chunks(
        SYSTEM, version, chunks, space=SPACE, chunker_version="test"
    )
    if ready:
        await uow.processing.transition_version(
            SYSTEM,
            version.id,
            expected=frozenset({ProcessingStatus.PENDING}),
            outcome=ProcessingOutcome.ready(chunk_count=len(chunks)),
        )
    return document


async def _lexical(
    uow: UnitOfWork,
    ctx: AccessContext,
    query: str,
    *,
    match: LexicalMatch = LexicalMatch.ANY,
    filters: SearchFilters = ALL,
) -> list[uuid.UUID]:
    candidates = await uow.search.lexical_candidates(
        ctx, query, match=match, filters=filters, limit=50
    )
    records = await uow.search.load_results(ctx, [c.chunk_id for c in candidates], filters=filters)
    return [records[c.chunk_id].document_id for c in candidates]


def _search(uow: UnitOfWork) -> HybridSearch:
    """The production use case over the test's transaction."""

    class _Factory:
        def __call__(self) -> _Factory:
            return self

        async def __aenter__(self) -> UnitOfWork:
            return uow

        async def __aexit__(self, *_: object) -> None:
            return None

    return HybridSearch(_Factory(), PROVIDER, SearchPolicy(ef_search=40))


class TestFullTextRetrieval:
    async def test_stemming_matches_other_forms_of_a_word(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        rotation = await _document(uow, ctx, "Tokens", ["Refresh tokens rotate on every use."])
        assert await _lexical(uow, ctx, "rotating token") == [rotation.id]

    async def test_stop_words_alone_match_nothing_without_error(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await _document(uow, ctx, "Tokens", ["Refresh tokens rotate on every use."])
        assert await _lexical(uow, ctx, "the and of") == []

    async def test_an_error_code_is_matched_as_written(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        codes = await _document(
            uow, ctx, "Errors", ["ERR-4012 means the token expired.", "ERR-5031 is a timeout."]
        )
        candidates = await uow.search.lexical_candidates(
            ctx, "ERR-4012", match=LexicalMatch.ANY, filters=ALL, limit=10
        )
        records = await uow.search.load_results(ctx, [c.chunk_id for c in candidates], filters=ALL)
        assert records[candidates[0].chunk_id].content.startswith("ERR-4012")
        assert records[candidates[0].chunk_id].document_id == codes.id

    async def test_any_matches_partial_questions_that_all_rejects(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        leave = await _document(uow, ctx, "Leave", ["Parental leave is sixteen weeks at full pay."])
        question = "how many weeks is parental leave for a newborn"
        assert await _lexical(uow, ctx, question, match=LexicalMatch.ALL) == []
        assert await _lexical(uow, ctx, question, match=LexicalMatch.ANY) == [leave.id]

    async def test_cover_density_ranks_the_closer_fuller_match_first(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        scattered = await _document(
            uow,
            ctx,
            "Scattered",
            ["Leave requests go to HR. Separately, the office has parental rooms and a pay desk."],
        )
        focused = await _document(uow, ctx, "Focused", ["Parental leave is paid at full pay."])
        ranked = await _lexical(uow, ctx, "parental leave pay")
        assert ranked[:2] == [focused.id, scattered.id]

    @pytest.mark.parametrize("match", list(LexicalMatch))
    async def test_the_rewritten_query_stays_indexable_by_gin(
        self, uow: UnitOfWork, ctx: AccessContext, match: LexicalMatch
    ) -> None:
        """The risk this guards: the OR rewrite (cast, replace, cast back) must
        fold to a constant `tsquery`, or `search_vector @@ ...` could never use
        `ix_chunks_search_vector` and every lexical search would scan.

        Which plan wins for the *whole* query is the planner's call and depends
        on data distribution -- for a small workspace the workspace B-tree is
        cheaper, correctly -- so that is not asserted. Indexability is."""
        await _document(uow, ctx, "Tokens", ["Refresh tokens rotate on every use."])
        await uow.session.execute(text("SET LOCAL enable_seqscan = off"))
        statement = select(ChunkRow.id).where(
            ChunkRow.search_vector.op("@@")(ts_query("rotating refresh tokens", match))
        )
        captured: list[tuple[str, object]] = []
        connection = await uow.session.connection()
        assert connection.sync_connection is not None

        def capture(_c: object, _cur: object, sql: str, params: object, *_: object) -> None:
            captured.append((sql, params))

        event.listen(connection.sync_connection, "before_cursor_execute", capture)
        try:
            await uow.session.execute(statement)
        finally:
            event.remove(connection.sync_connection, "before_cursor_execute", capture)
        sql, params = captured[-1]
        plan = await connection.exec_driver_sql(f"EXPLAIN {sql}", params)  # type: ignore[arg-type]
        assert "ix_chunks_search_vector" in "\n".join(row[0] for row in plan)


class TestAuthorizationBoundaries:
    async def test_a_verbatim_copy_of_another_tenants_passage_retrieves_nothing(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        """The attack: the attacker searches with the victim's exact text, so
        the victim's chunk is the perfect match lexically *and* its vector is
        identical to the query vector. Neither retriever may return it."""
        secret = "Project Halcyon acquires Globex for 41 million on 3 March."
        await _document(uow, other_ctx, "Board minutes", [secret])

        assert await _lexical(uow, ctx, secret) == []
        semantic = await uow.search.semantic_candidates(
            ctx, PROVIDER.vector(secret), space=SPACE, filters=ALL, limit=50, ef_search=100
        )
        assert semantic == []
        for mode in RetrievalMode:
            response = await _search(uow).execute(ctx, SearchQuery(text=secret, mode=mode))
            assert response.results == (), mode

    async def test_a_document_filter_cannot_reach_into_another_workspace(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        theirs = await _document(uow, other_ctx, "Board minutes", ["Project Halcyon terms."])
        filters = SearchFilters(document_ids=frozenset({theirs.id}))
        response = await _search(uow).execute(
            ctx, SearchQuery(text="Project Halcyon terms", filters=filters)
        )
        assert response.results == ()

    async def test_materialising_a_foreign_chunk_id_returns_nothing(
        self,
        uow: UnitOfWork,
        ctx: AccessContext,
        other_ctx: AccessContext,
    ) -> None:
        await _document(uow, other_ctx, "Board minutes", ["Project Halcyon terms."])
        (foreign_id,) = (
            await uow.session.execute(
                text("SELECT id FROM chunks WHERE workspace_id = :w"),
                {"w": other_ctx.workspace_id},
            )
        ).scalars()
        assert await uow.search.load_results(ctx, [foreign_id], filters=ALL) == {}
        assert foreign_id in await uow.search.load_results(other_ctx, [foreign_id], filters=ALL)

    async def test_unsearchable_versions_are_excluded_from_both_retrievers(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        passage = "Quarterly revenue targets for the EMEA region."
        visible = await _document(uow, ctx, "Visible", [passage])
        await _document(uow, ctx, "Pending", [passage], ready=False)
        deleted = await _document(uow, ctx, "Deleted", [passage])
        await uow.documents.soft_delete(ctx, deleted.id)
        demoted = await _document(uow, ctx, "Demoted", [passage])
        assert demoted.current_version is not None
        await uow.session.execute(
            text("UPDATE document_versions SET is_current = false WHERE id = :v"),
            {"v": demoted.current_version.id},
        )

        assert await _lexical(uow, ctx, passage) == [visible.id]
        response = await _search(uow).execute(ctx, SearchQuery(text=passage, limit=50))
        assert {r.document.id for r in response.results} == {visible.id}


class TestHybridEndToEnd:
    async def test_results_carry_real_locations_and_both_retrievers_evidence(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        manual = await _document(
            uow,
            ctx,
            "Runbook",
            [
                "Page the on-call engineer for a SEV1 incident within five minutes.",
                "Postmortems are written within five working days.",
            ],
        )

        response = await _search(uow).execute(ctx, SearchQuery(text="SEV1 incident on-call"))

        assert response.retrievers == (RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC)
        top = response.results[0]
        assert top.document.id == manual.id and top.chunk.ordinal == 0
        assert (top.location.page_from, top.location.heading_path) == (1, "Runbook")
        assert (top.location.char_start, top.location.char_end) == (0, len(top.chunk.text))
        assert top.matched_by is MatchedBy.BOTH
        assert top.relevance.lexical is not None and top.relevance.lexical.score > 0
        assert top.relevance.semantic is not None and 0 < top.relevance.semantic.score <= 1
