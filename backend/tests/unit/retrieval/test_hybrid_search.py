"""The hybrid search use case, end to end on fakes: real upload, real
parsing and chunking, real fusion; in-memory storage and brute-force retrievers.

The fake embedding provider is lexical underneath (hashed word counts), so a
`_ParaphraseEmbedder` stands in for semantic capability where a test needs
the two retrievers to disagree: it rewrites known paraphrases before
embedding, exactly as a real model maps them close together.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence

import pytest

from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.retrieval.hybrid_search import (
    SEMANTIC_UNAVAILABLE,
    HybridSearch,
    SearchPolicy,
)
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import AIProviderUnavailableError, ConfigurationError, ValidationError
from orbit.domain.models.entities import Document
from orbit.domain.retrieval import (
    MAX_SEARCH_OFFSET,
    ChunkRecord,
    LexicalMatch,
    MatchedBy,
    RetrievalMethod,
    RetrievalMode,
    SearchFilters,
    SearchQuery,
)
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from tests.unit.fakes.fake_processing import FakeSearchRepository
from tests.unit.processing.harness import Pipeline, build_pipeline

LEAVE = (
    "Parental leave is sixteen weeks at full pay. It can start up to four weeks "
    "before the expected birth and is booked through the HR portal. "
) * 3
ERRORS = (
    "Error ERR-4012 means the access token expired; request a new token. "
    "Error ERR-5031 means an upstream service timed out; retry with backoff. "
) * 3
EXPENSES = (
    "Expense claims need an itemised receipt. Claims above five hundred dollars "
    "need approval from a budget holder before reimbursement. "
) * 3


@dataclasses.dataclass
class _ParaphraseEmbedder:
    """The fake provider, plus a table of paraphrases a real model would
    place near their canonical wording. Counts query embeddings."""

    rewrites: dict[str, str]
    inner: FakeEmbeddingProvider = dataclasses.field(
        default_factory=lambda: FakeEmbeddingProvider(dimensions=32)
    )
    failure: BaseException | None = None
    query_calls: int = 0

    @property
    def space(self) -> EmbeddingSpace:
        return self.inner.space

    @property
    def max_batch_size(self) -> int:
        return self.inner.max_batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self.inner.max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return await self.inner.embed_documents(texts)

    async def embed_query(self, text: str) -> Sequence[float]:
        self.query_calls += 1
        if self.failure is not None:
            raise self.failure
        for phrase, canonical in self.rewrites.items():
            text = text.replace(phrase, canonical)
        return await self.inner.embed_query(text)


async def _corpus(
    rewrites: dict[str, str] | None = None,
) -> tuple[Pipeline, dict[str, Document], HybridSearch, _ParaphraseEmbedder]:
    pipeline = await build_pipeline()
    documents = {
        "leave": await pipeline.upload("leave.txt", LEAVE.encode()),
        "errors": await pipeline.upload("errors.txt", ERRORS.encode()),
        "expenses": await pipeline.upload("expenses.txt", EXPENSES.encode()),
    }
    await pipeline.drain()
    embedder = _ParaphraseEmbedder(rewrites or {})
    search = HybridSearch(pipeline.uow_factory, embedder, SearchPolicy(ef_search=40))
    return pipeline, documents, search, embedder


async def _other_tenant(pipeline: Pipeline) -> AccessContext:
    workspace = await CreateWorkspace(pipeline.uow_factory).execute(
        name="Globex", created_by_user_id=pipeline.ctx.user_id
    )
    return AccessContext(user_id=pipeline.ctx.user_id, workspace_id=workspace.id, role=Role.OWNER)


class TestStructuredResults:
    async def test_every_result_names_its_document_version_chunk_location_and_ranking(
        self,
    ) -> None:
        pipeline, documents, search, _ = await _corpus()

        response = await search.execute(pipeline.ctx, SearchQuery(text="parental leave weeks"))

        top = response.results[0]
        version = pipeline.version(documents["leave"])
        (stored,) = [c for c in pipeline.chunks(documents["leave"]) if c.chunk_id == top.chunk.id]
        assert top.rank == 1
        assert (top.document.id, top.document.title) == (documents["leave"].id, "leave.txt")
        assert (top.version.id, top.version.version_number) == (version.id, 1)
        assert (top.chunk.ordinal, top.chunk.text) == (stored.ordinal, stored.text)
        assert (top.location.char_start, top.location.char_end) == (
            stored.char_start,
            stored.char_end,
        )
        assert top.matched_by is MatchedBy.BOTH
        assert top.relevance.lexical is not None and top.relevance.lexical.rank == 1
        assert top.relevance.semantic is not None and top.relevance.semantic.rank == 1
        assert [r.rank for r in response.results] == list(range(1, len(response.results) + 1))
        scores = [r.relevance.score for r in response.results]
        assert scores == sorted(scores, reverse=True)
        assert (response.fusion.algorithm, response.fusion.k) == ("reciprocal_rank_fusion", 60)

    async def test_the_response_reports_the_normalised_query_and_candidate_counts(self) -> None:
        pipeline, _, search, _ = await _corpus()
        response = await search.execute(
            pipeline.ctx, SearchQuery(text="  Expense\u200b   receipt ")
        )
        assert response.query == "Expense receipt"
        assert set(response.candidates) == {RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC}


class TestHybridRanking:
    async def test_both_retrievers_contribute_results_the_other_missed(self) -> None:
        pipeline, documents, search, _ = await _corpus(rewrites={"newborn": "parental leave birth"})

        response = await search.execute(pipeline.ctx, SearchQuery(text="newborn ERR-4012"))

        by_document = {r.document.id: r for r in response.results}
        assert response.retrievers == (RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC)
        leave, errors = by_document[documents["leave"].id], by_document[documents["errors"].id]
        # "newborn" appears in no document: only the semantic path finds leave.
        assert leave.matched_by is MatchedBy.SEMANTIC and leave.relevance.lexical is None
        # The error code is found by both.
        assert errors.matched_by is MatchedBy.BOTH

    async def test_agreement_outranks_a_single_retrievers_favourite(self) -> None:
        pipeline, documents, search, _ = await _corpus(rewrites={"payout": "reimbursement"})
        response = await search.execute(
            pipeline.ctx, SearchQuery(text="receipt payout budget holder")
        )
        assert response.results[0].document.id == documents["expenses"].id
        assert response.results[0].matched_by is MatchedBy.BOTH

    @pytest.mark.parametrize(
        ("mode", "retrievers"),
        [
            (RetrievalMode.LEXICAL, (RetrievalMethod.LEXICAL,)),
            (RetrievalMode.SEMANTIC, (RetrievalMethod.SEMANTIC,)),
        ],
    )
    async def test_a_single_retriever_mode_uses_only_that_retriever(
        self, mode: RetrievalMode, retrievers: tuple[RetrievalMethod, ...]
    ) -> None:
        pipeline, _, search, embedder = await _corpus()
        response = await search.execute(pipeline.ctx, SearchQuery(text="token expired", mode=mode))
        assert response.retrievers == retrievers
        assert all(r.matched_by.value == retrievers[0].value for r in response.results)
        assert embedder.query_calls == (1 if mode is RetrievalMode.SEMANTIC else 0)

    async def test_all_terms_mode_requires_every_term(self) -> None:
        pipeline, _, _, embedder = await _corpus()
        strict = HybridSearch(
            pipeline.uow_factory, embedder, SearchPolicy(lexical_match=LexicalMatch.ALL)
        )
        loose = HybridSearch(pipeline.uow_factory, embedder, SearchPolicy())
        query = SearchQuery(text="receipt parental", mode=RetrievalMode.LEXICAL)

        assert (await strict.execute(pipeline.ctx, query)).results == ()
        assert len((await loose.execute(pipeline.ctx, query)).results) >= 2

    async def test_limit_caps_results_after_fusion(self) -> None:
        pipeline, _, search, _ = await _corpus()
        response = await search.execute(pipeline.ctx, SearchQuery(text="the", limit=1))
        assert len(response.results) == 1


class TestDegradation:
    async def test_a_provider_outage_answers_lexically_and_says_so(self) -> None:
        pipeline, documents, search, embedder = await _corpus()
        embedder.failure = AIProviderUnavailableError("503")

        response = await search.execute(pipeline.ctx, SearchQuery(text="ERR-5031 timeout"))

        assert response.mode is RetrievalMode.HYBRID
        assert response.retrievers == (RetrievalMethod.LEXICAL,)
        assert response.degraded == SEMANTIC_UNAVAILABLE
        assert response.results[0].document.id == documents["errors"].id
        assert all(r.matched_by is MatchedBy.LEXICAL for r in response.results)

    async def test_semantic_only_mode_does_not_pretend(self) -> None:
        pipeline, _, search, embedder = await _corpus()
        embedder.failure = AIProviderUnavailableError("503")
        with pytest.raises(AIProviderUnavailableError):
            await search.execute(
                pipeline.ctx, SearchQuery(text="timeout", mode=RetrievalMode.SEMANTIC)
            )

    async def test_a_misconfiguration_is_not_degraded_around(self) -> None:
        pipeline, _, search, embedder = await _corpus()
        embedder.failure = ConfigurationError("bad key")
        with pytest.raises(ConfigurationError):
            await search.execute(pipeline.ctx, SearchQuery(text="timeout"))

    async def test_an_invalid_query_costs_no_provider_call(self) -> None:
        pipeline, _, search, embedder = await _corpus()
        with pytest.raises(ValidationError):
            await search.execute(pipeline.ctx, SearchQuery(text=" \u200b "))
        assert embedder.query_calls == 0


class TestAuthorizationBoundaries:
    async def test_identical_text_in_another_workspace_is_never_returned(self) -> None:
        """The attack: copy a victim's passage verbatim and search for it, so it
        is the nearest neighbour by any measure. It must still not appear."""
        pipeline, _, search, _ = await _corpus()
        attacker = await _other_tenant(pipeline)

        for mode in RetrievalMode:
            response = await search.execute(attacker, SearchQuery(text=LEAVE, limit=50, mode=mode))
            assert response.results == (), mode

    async def test_each_tenant_sees_only_its_own_copy_of_shared_text(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        other = await _other_tenant(pipeline)
        theirs = await pipeline.upload("leave.txt", LEAVE.encode(), ctx=other)
        await pipeline.drain()

        mine = await search.execute(pipeline.ctx, SearchQuery(text="parental leave", limit=50))
        yours = await search.execute(other, SearchQuery(text="parental leave", limit=50))

        assert {r.document.id for r in mine.results} <= {d.id for d in documents.values()}
        assert {r.document.id for r in yours.results} == {theirs.id}

    async def test_a_document_filter_naming_another_tenants_document_matches_nothing(
        self,
    ) -> None:
        pipeline, _, search, _ = await _corpus()
        other = await _other_tenant(pipeline)
        theirs = await pipeline.upload(
            "secret.txt", b"project halcyon merger terms " * 20, ctx=other
        )
        await pipeline.drain()

        response = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="project halcyon merger",
                filters=SearchFilters(document_ids=frozenset({theirs.id})),
            ),
        )
        assert response.results == ()

    async def test_a_document_filter_restricts_within_the_workspace(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        response = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="weeks receipt token",
                filters=SearchFilters(document_ids=frozenset({documents["expenses"].id})),
            ),
        )
        assert response.results
        assert {r.document.id for r in response.results} == {documents["expenses"].id}

    async def test_a_deleted_document_disappears_from_every_retriever(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        await DeleteDocument(pipeline.uow_factory).execute(pipeline.ctx, documents["leave"].id)
        for mode in RetrievalMode:
            response = await search.execute(
                pipeline.ctx, SearchQuery(text="parental leave", limit=50, mode=mode)
            )
            assert documents["leave"].id not in {r.document.id for r in response.results}

    async def test_a_record_from_another_workspace_is_never_exposed_even_if_loaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defence in depth: should the materialising query ever return a
        foreign row, the use case drops it rather than exposing it."""
        pipeline, _, search, _ = await _corpus()
        foreign = await _other_tenant(pipeline)
        original = FakeSearchRepository.load_results

        async def leaky(
            self: FakeSearchRepository,
            ctx: AccessContext,
            chunk_ids: object,
            *,
            filters: SearchFilters,
        ) -> dict[object, ChunkRecord]:
            records = dict(await original(self, ctx, chunk_ids, filters=filters))  # type: ignore[arg-type]
            return {
                key: dataclasses.replace(record, workspace_id=foreign.workspace_id)
                for key, record in records.items()
            }

        monkeypatch.setattr(FakeSearchRepository, "load_results", leaky)
        response = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))
        assert response.results == ()

    async def test_viewers_may_search(self) -> None:
        pipeline, _, search, _ = await _corpus()
        viewer = dataclasses.replace(pipeline.ctx, role=Role.VIEWER)
        assert (await search.execute(viewer, SearchQuery(text="receipt"))).results


class TestFilters:
    """Filters narrow; nothing widens.

    Folder and tag semantics are checked against the document list's meaning
    on purpose (`DocumentListQuery`): a folder is the documents directly in
    it, and tags are conjunctive. Two surfaces disagreeing about what a saved
    filter means is the bug these pin down.
    """

    async def test_a_folder_filter_returns_only_that_folders_documents(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        folder_id = _file(pipeline, documents["expenses"])

        response = await search.execute(
            pipeline.ctx,
            SearchQuery(text="weeks receipt token", filters=SearchFilters(folder_id=folder_id)),
        )

        assert response.results
        assert {r.document.id for r in response.results} == {documents["expenses"].id}

    async def test_unfiled_excludes_everything_in_a_folder(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        _file(pipeline, documents["expenses"])

        response = await search.execute(
            pipeline.ctx,
            SearchQuery(text="weeks receipt token", filters=SearchFilters(unfiled=True)),
        )

        assert response.results
        assert documents["expenses"].id not in {r.document.id for r in response.results}

    async def test_a_folder_of_another_tenant_matches_nothing(self) -> None:
        pipeline, _, search, _ = await _corpus()
        other = await _other_tenant(pipeline)
        theirs = await pipeline.upload(
            "theirs.txt", b"project halcyon merger terms " * 20, ctx=other
        )
        await pipeline.drain()
        folder_id = _file(pipeline, theirs)

        response = await search.execute(
            pipeline.ctx,
            SearchQuery(text="project halcyon merger", filters=SearchFilters(folder_id=folder_id)),
        )
        assert response.results == ()

    async def test_tags_are_conjunctive(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        policy, finance = uuid.uuid4(), uuid.uuid4()
        _tag(pipeline, documents["expenses"], policy, finance)
        _tag(pipeline, documents["leave"], policy)
        query = "weeks receipt token"

        either = await search.execute(
            pipeline.ctx,
            SearchQuery(text=query, filters=SearchFilters(tag_ids=frozenset({policy}))),
        )
        both = await search.execute(
            pipeline.ctx,
            SearchQuery(text=query, filters=SearchFilters(tag_ids=frozenset({policy, finance}))),
        )

        assert {r.document.id for r in either.results} == {
            documents["expenses"].id,
            documents["leave"].id,
        }
        assert {r.document.id for r in both.results} == {documents["expenses"].id}

    async def test_a_content_type_filter_selects_by_stored_type(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        notes = await pipeline.upload("notes.md", b"# Receipts. Keep every receipt. " * 20)
        await pipeline.drain()

        markdown = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="receipt",
                limit=50,
                filters=SearchFilters(content_types=frozenset({"text/markdown"})),
            ),
        )
        plain = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="receipt",
                limit=50,
                filters=SearchFilters(content_types=frozenset({"text/plain"})),
            ),
        )

        assert {r.document.id for r in markdown.results} == {notes.id}
        assert notes.id not in {r.document.id for r in plain.results}
        assert documents["expenses"].id in {r.document.id for r in plain.results}

    async def test_filters_compose(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        tag = uuid.uuid4()
        _tag(pipeline, documents["leave"], tag)
        _tag(pipeline, documents["expenses"], tag)
        _file(pipeline, documents["expenses"])

        response = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="weeks receipt",
                filters=SearchFilters(tag_ids=frozenset({tag}), unfiled=True),
            ),
        )
        assert {r.document.id for r in response.results} == {documents["leave"].id}

    async def test_the_response_says_whether_it_was_narrowed(self) -> None:
        pipeline, documents, search, _ = await _corpus()

        plain = await search.execute(pipeline.ctx, SearchQuery(text="receipt"))
        narrowed = await search.execute(
            pipeline.ctx,
            SearchQuery(
                text="receipt",
                filters=SearchFilters(document_ids=frozenset({documents["expenses"].id})),
            ),
        )

        assert plain.filtered is False
        assert narrowed.filtered is True

    def test_a_folder_and_unfiled_together_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            SearchFilters(folder_id=uuid.uuid4(), unfiled=True)

    def test_an_empty_filter_list_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            SearchFilters(tag_ids=frozenset())
        with pytest.raises(ValidationError):
            SearchFilters(content_types=frozenset())


class TestPagination:
    """A page is a slice of one ranking, not a fresh search.

    RRF ranks whatever pool it is given, so a later page is served by fusing
    a deeper pool and slicing it. These pin the two properties that makes
    observable: pages do not overlap, and `rank` keeps counting.
    """

    async def test_pages_partition_the_ranking_without_overlap_or_gaps(self) -> None:
        pipeline, search = await _deep_corpus()
        whole = await search.execute(pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=6))
        assert len(whole.results) == 6

        first = await search.execute(pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=3))
        second = await search.execute(
            pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=3, offset=3)
        )

        assert [r.chunk.id for r in first.results] == [r.chunk.id for r in whole.results[:3]]
        assert [r.chunk.id for r in second.results] == [r.chunk.id for r in whole.results[3:]]

    async def test_rank_continues_across_pages(self) -> None:
        pipeline, search = await _deep_corpus()
        second = await search.execute(
            pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=3, offset=3)
        )
        assert [r.rank for r in second.results] == [4, 5, 6]
        assert second.offset == 3

    async def test_has_more_distinguishes_a_full_page_from_the_last_page(self) -> None:
        pipeline, search = await _deep_corpus()
        assert (
            await search.execute(pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=3))
        ).has_more is True
        assert (
            await search.execute(pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=50))
        ).has_more is False

    async def test_an_offset_past_the_end_is_an_empty_page_not_an_error(self) -> None:
        pipeline, search = await _deep_corpus()
        response = await search.execute(
            pipeline.ctx, SearchQuery(text=_PAGED_TERM, limit=10, offset=100)
        )
        assert response.results == ()
        assert response.has_more is False

    def test_a_deeper_page_retrieves_a_deeper_candidate_pool(self) -> None:
        policy = SearchPolicy(candidates_per_retriever=50, max_candidates_per_retriever=250)
        assert policy.depth_for(SearchQuery(text="x", limit=10)) == 60
        assert policy.depth_for(SearchQuery(text="x", limit=10, offset=100)) == 160
        # Capped, so a deep page cannot become an unbounded scan.
        assert policy.depth_for(SearchQuery(text="x", limit=50, offset=200)) == 250

    @pytest.mark.parametrize("offset", [-1, MAX_SEARCH_OFFSET + 1])
    def test_an_out_of_range_offset_is_refused(self, offset: int) -> None:
        with pytest.raises(ValidationError):
            SearchQuery(text="anything", offset=offset)


class TestResultMetadata:
    async def test_each_result_carries_its_documents_type_and_last_change(self) -> None:
        pipeline, documents, search, _ = await _corpus()
        response = await search.execute(pipeline.ctx, SearchQuery(text="parental leave"))

        top = response.results[0]
        stored = pipeline.uow_factory.state.documents[documents["leave"].id]
        assert top.document.content_type == "text/plain"
        assert top.document.updated_at == stored.updated_at


#: A term every document in the deep corpus contains, so one query ranks them all.
_PAGED_TERM = "reimbursement"


async def _deep_corpus() -> tuple[Pipeline, HybridSearch]:
    """Eight one-chunk documents that all match one query.

    Paging needs a ranking longer than a page; the three-document corpus the
    other tests share is shorter than the first page.
    """
    pipeline = await build_pipeline()
    for index in range(8):
        body = f"Policy {index}: {_PAGED_TERM} for section {index} items. " * 6
        await pipeline.upload(f"policy-{index}.txt", body.encode())
    await pipeline.drain()
    search = HybridSearch(pipeline.uow_factory, _ParaphraseEmbedder({}), SearchPolicy(ef_search=40))
    return pipeline, search


def _file(pipeline: Pipeline, document: Document) -> uuid.UUID:
    """Put a document in a (new) folder, in the fake's state."""
    folder_id = uuid.uuid4()
    state = pipeline.uow_factory.state
    state.documents[document.id] = dataclasses.replace(
        state.documents[document.id], folder_id=folder_id
    )
    return folder_id


def _tag(pipeline: Pipeline, document: Document, *tag_ids: uuid.UUID) -> None:
    for tag_id in tag_ids:
        pipeline.uow_factory.state.document_tags.add((document.id, tag_id))
