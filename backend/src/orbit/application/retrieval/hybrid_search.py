"""Search a workspace: lexical and semantic retrieval, fused (ADR-0005, ADR-0021).

```
SearchQuery
  ├─ normalize_query
  ├─ lexical:  plainto_tsquery → GIN → ts_rank_cd          ─┐  concurrently with
  └─ semantic: embed_query → HNSW (active space) → cosine  ─┘  the embedding call
                   │
             candidate lists (ids + native scores, workspace-filtered in SQL)
                   │
             Reciprocal Rank Fusion, k = 60
                   │
             top K ids → load_results (workspace + visibility re-checked) → results
```

Every stage is replaceable in isolation: the retrievers are repository
methods, fusion is a pure function, and the provider is a port.

**Honest labelling.** A hybrid request whose embedding call fails with a
provider outage is answered from the lexical list alone, and the response says
so (`retrievers == (lexical,)`, `degraded == "semantic_unavailable"`) rather
than presenting a lexical ranking as hybrid. A misconfiguration is not
degraded around: it raises.

**The query vector is cached** (ADR-0024), keyed by workspace and embedding
space. A hit skips the provider entirely, which is why a repeated search stays
fast and free; a miss, or a Redis outage, takes the path above unchanged.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.core.logging import get_logger, operation
from orbit.core.metrics import (
    SEARCH_DEGRADED,
    SEARCH_DURATION,
    SEARCH_STAGE_DURATION,
    observe_duration,
)
from orbit.domain.access import AccessContext, Permission
from orbit.domain.embeddings import to_indexable_vector
from orbit.domain.errors import AIProviderUnavailableError, OrbitError
from orbit.domain.ports.cache import QueryVectorCache
from orbit.domain.ports.embeddings import EmbeddingProvider
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.retrieval import (
    RRF_K,
    Candidate,
    Fusion,
    LexicalMatch,
    RankedList,
    RetrievalMethod,
    RetrievalMode,
    SearchQuery,
    SearchResponse,
    SearchResult,
    normalize_query,
    reciprocal_rank_fusion,
)

logger = get_logger(__name__)

_HTTP_SERVER_ERROR = 500

SEMANTIC_UNAVAILABLE = "semantic_unavailable"


@dataclass(frozen=True, slots=True)
class SearchPolicy:
    #: How deep each retriever looks before fusion. Deeper than the result
    #: limit so a chunk ranked modestly by both retrievers can still win.
    candidates_per_retriever: int = 50
    lexical_match: LexicalMatch = LexicalMatch.ANY
    ef_search: int = 100
    rrf_k: int = RRF_K
    #: A hard ceiling on the candidate pool, however deep a page reaches.
    max_candidates_per_retriever: int = 250

    def depth_for(self, query: SearchQuery) -> int:
        """How many candidates each retriever must return for `query`.

        A later page is not a resumption: RRF ranks whatever pool it is given,
        so serving results 40-60 means fusing a pool deep enough to *contain*
        result 60 and slicing it. The pool is therefore the larger of the
        standard depth and the page's reach, with headroom so a chunk both
        retrievers rank modestly can still place on this page -- and a
        ceiling, so a deep page cannot turn into an unbounded scan.
        """
        return min(
            max(self.candidates_per_retriever, query.depth + self.candidates_per_retriever),
            self.max_candidates_per_retriever,
        )


class HybridSearch:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provider: EmbeddingProvider,
        policy: SearchPolicy,
        *,
        vector_cache: QueryVectorCache | None = None,
        rate_limit: AuthRateLimitGuard | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider = provider
        self._policy = policy
        self._vector_cache = vector_cache
        self._rate_limit = rate_limit

    async def execute(
        self,
        ctx: AccessContext,
        query: SearchQuery,
        *,
        client_ip: str | None = None,
        metered: bool = True,
    ) -> SearchResponse:
        """Run one search.

        `metered=False` is for the one internal caller that has already been
        limited under its own, stricter scope: `AnswerQuestion` searches on
        the way to an answer, and charging that search against the search
        budget too would mean a user asking questions gradually loses the
        ability to search. Every path that originates from a client leaves it
        at the default, so a new route cannot silently skip the limit.
        """
        began = time.perf_counter()
        outcome = "error"
        try:
            with operation("search.execute"):
                response = await self._execute(ctx, query, client_ip=client_ip, metered=metered)
        except OrbitError as exc:
            # A 4xx (rate limited, forbidden) is the caller's outcome, not the
            # system failing; folding it into "error" would make an abusive
            # client look like an outage.
            outcome = "rejected" if exc.http_status < _HTTP_SERVER_ERROR else "error"
            raise
        else:
            outcome = "degraded" if response.degraded else "ok"
            return response
        finally:
            SEARCH_DURATION.labels(mode=query.mode.value, outcome=outcome).observe(
                time.perf_counter() - began
            )

    async def _execute(
        self,
        ctx: AccessContext,
        query: SearchQuery,
        *,
        client_ip: str | None,
        metered: bool,
    ) -> SearchResponse:
        ctx.require(Permission.SEARCH_QUERY)
        # Before the embedding call and before any SQL: the limit exists to
        # stop the cost, so it has to gate entry rather than the expensive
        # part of it.
        if metered and self._rate_limit is not None:
            await self._rate_limit.check(identity=str(ctx.user_id), client_ip=client_ip)
        text = normalize_query(query.text)
        wants_lexical = query.mode in (RetrievalMode.HYBRID, RetrievalMode.LEXICAL)
        wants_semantic = query.mode in (RetrievalMode.HYBRID, RetrievalMode.SEMANTIC)

        depth = self._policy.depth_for(query)

        vector_task = asyncio.ensure_future(self._embed(ctx, text)) if wants_semantic else None
        lexical: Sequence[Candidate] = []
        try:
            if wants_lexical:
                lexical = await self._lexical(ctx, text, query, depth)
            vector = await vector_task if vector_task is not None else None
        except BaseException:
            if vector_task is not None:
                vector_task.cancel()
            raise

        degraded: str | None = None
        lists: list[RankedList] = []
        if wants_lexical:
            lists.append(RankedList(RetrievalMethod.LEXICAL, tuple(lexical)))
        if wants_semantic:
            if isinstance(vector, AIProviderUnavailableError):
                if query.mode is RetrievalMode.SEMANTIC:
                    raise vector
                degraded = SEMANTIC_UNAVAILABLE
                SEARCH_DEGRADED.labels(reason=degraded).inc()
                logger.warning("search.degraded", reason=degraded, error_code=vector.code)
            elif vector is not None:
                semantic = await self._semantic(ctx, vector, query, depth)
                lists.append(RankedList(RetrievalMethod.SEMANTIC, tuple(semantic)))

        ranked = reciprocal_rank_fusion(lists, k=self._policy.rrf_k)
        # One past the page, so "is there another page" is answered by what was
        # ranked rather than by guessing from a full page.
        page = ranked[query.offset : query.depth + 1]
        has_more = len(page) > query.limit
        page = page[: query.limit]

        async with self._uow_factory() as uow:
            with observe_duration(SEARCH_STAGE_DURATION, stage="hydrate"):
                records = await uow.search.load_results(
                    ctx, [c.chunk_id for c in page], filters=query.filters
                )

        results: list[SearchResult] = []
        for candidate in page:
            record = records.get(candidate.chunk_id)
            if record is None:
                # Deleted or superseded between retrieval and materialisation.
                continue
            if record.workspace_id != ctx.workspace_id:  # pragma: no cover -- SQL guarantees it
                # Belt and braces: the repository already filtered on this.
                # Reaching here is a defect, and the row is never exposed.
                logger.error(
                    "search.cross_workspace_result_blocked",
                    chunk_id=str(record.chunk_id),
                    workspace_id=str(ctx.workspace_id),
                )
                continue
            # Rank is the result's position in the whole ranking, not in the
            # page: "result 21" must stay result 21 on page two.
            results.append(SearchResult.build(query.offset + len(results) + 1, candidate, record))

        response = SearchResponse(
            query=text,
            mode=query.mode,
            retrievers=tuple(ranked_list.method for ranked_list in lists),
            degraded=degraded,
            fusion=Fusion(algorithm="reciprocal_rank_fusion", k=self._policy.rrf_k),
            candidates={ranked_list.method: len(ranked_list.candidates) for ranked_list in lists},
            results=tuple(results),
            offset=query.offset,
            has_more=has_more,
            filtered=query.filters.is_narrowed,
        )
        logger.info(
            "search.completed",
            mode=query.mode.value,
            retrievers=[method.value for method in response.retrievers],
            degraded=degraded,
            candidates={method.value: n for method, n in response.candidates.items()},
            offset=query.offset,
            has_more=has_more,
            results=len(results),
            matched_by={
                kind: sum(1 for r in results if r.matched_by.value == kind)
                for kind in ("lexical", "semantic", "both")
            },
        )
        return response

    async def _lexical(
        self, ctx: AccessContext, text: str, query: SearchQuery, depth: int
    ) -> Sequence[Candidate]:
        async with self._uow_factory() as uow:
            with observe_duration(SEARCH_STAGE_DURATION, stage="lexical"):
                return await uow.search.lexical_candidates(
                    ctx,
                    text,
                    match=self._policy.lexical_match,
                    filters=query.filters,
                    limit=depth,
                )

    async def _semantic(
        self, ctx: AccessContext, vector: Sequence[float], query: SearchQuery, depth: int
    ) -> Sequence[Candidate]:
        async with self._uow_factory() as uow:
            with observe_duration(SEARCH_STAGE_DURATION, stage="semantic"):
                return await uow.search.semantic_candidates(
                    ctx,
                    vector,
                    space=self._provider.space,
                    filters=query.filters,
                    limit=depth,
                    ef_search=self._policy.ef_search,
                )

    async def _embed(
        self, ctx: AccessContext, text: str
    ) -> Sequence[float] | AIProviderUnavailableError:
        """The query vector, or the outage that prevented it (returned, not
        raised, so the caller decides whether the request can degrade).

        The cache is consulted first and written after. Both calls are
        non-raising by the port's contract, so a Redis outage costs a provider
        round trip and nothing else -- note in particular that a cache miss
        during an AI outage still degrades to lexical below, because the
        provider error is what this returns either way.
        """
        with observe_duration(SEARCH_STAGE_DURATION, stage="query_embedding"):
            return await self._embed_query(ctx, text)

    async def _embed_query(
        self, ctx: AccessContext, text: str
    ) -> Sequence[float] | AIProviderUnavailableError:
        space = self._provider.space
        if self._vector_cache is not None:
            cached = await self._vector_cache.get(
                workspace_id=ctx.workspace_id, space=space, text=text
            )
            if cached is not None:
                return cached

        try:
            vector = to_indexable_vector(await self._provider.embed_query(text), space)
        except AIProviderUnavailableError as exc:
            return exc

        if self._vector_cache is not None:
            await self._vector_cache.put(
                workspace_id=ctx.workspace_id, space=space, text=text, vector=vector
            )
        return vector
