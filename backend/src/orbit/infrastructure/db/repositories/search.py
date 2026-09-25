"""Hybrid retrieval over PostgreSQL: full-text search and pgvector (ADR-0005, ADR-0021).

Both retrievers share one visibility predicate, written once below: the
caller's workspace, the request's filters (documents, folder, tags, content
type), the current version, READY, and an undeleted, unarchived document. It
is part of each query -- never applied to results afterwards -- so another
tenant's chunk is never a candidate, however similar it is (ADR-0004), and a
filter can only ever narrow what the workspace predicate already allows.

**Lexical.** The stored, generated `search_vector` (`to_tsvector('english',
content)`, GIN-indexed) matched against the query parsed by
`plainto_tsquery('english', ...)`: the same stemmer and stop-word list on both
sides, and no query syntax a user can get wrong. For `LexicalMatch.ANY` the
parsed query's `&` operators become `|`, so a chunk needs *some* of the terms
rather than all of them; the parse has already quoted every lexeme, so the
rewrite cannot change what a term means.

Lexical score, in [0, (n + 1) / n) for a query of n distinct terms:

    score = (terms matched + (cd(v, all_terms) + cd(v, any_term)) / 2) / n

where `terms matched` counts the query's distinct lexemes present in the
chunk and `cd` is `ts_rank_cd(..., 32)`, cover density normalised to [0, 1).

* **Coverage first.** A chunk containing more of the query's terms always
  outranks one containing fewer.
* **Proximity next.** `cd(v, all_terms)` scores the tightest spans containing
  *every* term, so "parental leave ... pay" in one sentence beats the same
  words scattered across a paragraph. It is zero unless all terms are present.
* **Frequency last.** `cd(v, any_term)` -- under OR every single matching term
  is its own cover, so this is effectively an occurrence count.

`ts_rank_cd` on the OR query alone -- the obvious first choice -- has no
proximity or coverage signal at all, and ranks a chunk that repeats one term
above one that answers the whole question. That was measured
(docs/benchmarks/retrieval.md) and is pinned by integration tests.

**Semantic.** Cosine distance (`<=>`) over `vector_cosine_ops`, restricted to
the active embedding space. Both providers emit unit-length vectors, where
cosine and inner product rank identically; cosine is kept because it stays
correct for a provider that does not normalise. `hnsw.iterative_scan =
relaxed_order` keeps walking the graph until enough chunks pass the filter --
without it, recall under the workspace predicate measured 0.94
(docs/benchmarks/vector-search.md) -- and the small result is re-sorted.
Small workspaces use the workspace B-tree and an exact sort instead, which is
the planner's choice and the right one.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import ColumnElement, Text, and_, cast, exists, func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.domain.access import AccessContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.retrieval import Candidate, ChunkRecord, LexicalMatch, SearchFilters
from orbit.infrastructure.db.models import Chunk as ChunkRow
from orbit.infrastructure.db.models import Document as DocumentRow
from orbit.infrastructure.db.models import DocumentTag as DocumentTagRow
from orbit.infrastructure.db.models import DocumentVersion as VersionRow
from orbit.infrastructure.db.models.content import ProcessingStatus as StatusRow

#: The text-search configuration `chunks.search_vector` is generated with. The
#: query must be parsed with the same one, or stemming disagrees.
TEXT_SEARCH_CONFIG = "english"


def _visible(ctx: AccessContext, filters: SearchFilters) -> ColumnElement[bool]:
    """The one definition of "this caller may see this chunk now"."""
    conditions: list[ColumnElement[bool]] = [
        ChunkRow.workspace_id == ctx.workspace_id,
        VersionRow.is_current.is_(True),
        VersionRow.status == StatusRow.READY,
        DocumentRow.deleted_at.is_(None),
        # An archived document is out of the working set, and so out of every
        # answer: a question must not cite a source the user has put away.
        # Its chunks stay indexed, so restoring it is instant.
        DocumentRow.archived_at.is_(None),
    ]
    if filters.document_ids is not None:
        conditions.append(ChunkRow.document_id.in_(sorted(filters.document_ids)))
    if filters.folder_id is not None:
        conditions.append(DocumentRow.folder_id == filters.folder_id)
    if filters.unfiled:
        conditions.append(DocumentRow.folder_id.is_(None))
    if filters.content_types is not None:
        conditions.append(VersionRow.content_type.in_(sorted(filters.content_types)))
    if filters.tag_ids is not None:
        # One EXISTS per tag rather than a join and a count: "carries *all* of
        # these" is a conjunction, and each EXISTS stays on the index
        # `ix_document_tags_workspace_id_tag_id`. Matches how the document
        # list filters, so the two cannot disagree about what a tag filter
        # means.
        conditions.extend(
            exists().where(
                DocumentTagRow.workspace_id == ctx.workspace_id,
                DocumentTagRow.document_id == ChunkRow.document_id,
                DocumentTagRow.tag_id == tag_id,
            )
            for tag_id in sorted(filters.tag_ids)
        )
    return and_(*conditions)


def _joined(query: Any) -> Any:  # noqa: ANN401 -- a SQLAlchemy Select
    return query.join(VersionRow, VersionRow.id == ChunkRow.document_version_id).join(
        DocumentRow, DocumentRow.id == ChunkRow.document_id
    )


def ts_query(text: str, match: LexicalMatch) -> ColumnElement[Any]:
    parsed = func.plainto_tsquery(TEXT_SEARCH_CONFIG, text)
    if match is LexicalMatch.ALL:
        return parsed
    # Cast straight back to `tsquery`, not through `to_tsquery`: the lexemes are
    # already stemmed, and stemming them a second time is not always a no-op.
    return cast(func.replace(cast(parsed, Text), " & ", " | "), TSQUERY)


class SqlSearchRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        iterative_scan: str = "relaxed_order",
        lexical_ranking: str = "coverage",
    ) -> None:
        # Both knobs are injectable only so the benchmarks can measure the
        # alternatives (`off`, `ts_rank_cd`); production uses the defaults.
        self._session = session
        self._iterative_scan = iterative_scan
        self._lexical_ranking = lexical_ranking

    async def lexical_candidates(
        self,
        ctx: AccessContext,
        text: str,
        *,
        match: LexicalMatch,
        filters: SearchFilters,
        limit: int,
    ) -> Sequence[Candidate]:
        query = ts_query(text, match)
        frequency = func.ts_rank_cd(ChunkRow.search_vector, query, 32)
        score: ColumnElement[Any]
        if self._lexical_ranking == "ts_rank_cd":
            score = frequency
        else:
            # The query's distinct lexemes, from the same parser and
            # dictionaries that built `search_vector`.
            terms = func.tsvector_to_array(func.to_tsvector(TEXT_SEARCH_CONFIG, text))
            chunk_terms = (
                func.unnest(func.tsvector_to_array(ChunkRow.search_vector))
                .table_valued("term")
                .render_derived(name="chunk_terms")
            )
            matched = (
                select(func.count())
                .select_from(chunk_terms)
                .where(chunk_terms.c.term == func.any(terms))
                .scalar_subquery()
            )
            proximity = func.ts_rank_cd(
                ChunkRow.search_vector, ts_query(text, LexicalMatch.ALL), 32
            )
            score = (matched + (proximity + frequency) / 2) / func.greatest(
                func.cardinality(terms), 1
            )
        ranked = score.label("score")
        rows = await self._session.execute(
            _joined(select(ChunkRow.id, ranked))
            .where(_visible(ctx, filters), ChunkRow.search_vector.op("@@")(query))
            .order_by(ranked.desc(), ChunkRow.id)
            .limit(limit)
        )
        return [Candidate(chunk_id=chunk_id, score=float(value)) for chunk_id, value in rows]

    async def semantic_candidates(  # noqa: PLR0913 -- keyword-only search parameters
        self,
        ctx: AccessContext,
        vector: Sequence[float],
        *,
        space: EmbeddingSpace,
        filters: SearchFilters,
        limit: int,
        ef_search: int,
    ) -> Sequence[Candidate]:
        # Transaction-local, so a pooled connection never carries these into
        # an unrelated query. `set_config` rather than `SET LOCAL`, which
        # cannot take a bound parameter.
        await self._session.execute(
            select(
                func.set_config("hnsw.ef_search", str(max(ef_search, limit)), True),
                func.set_config("hnsw.iterative_scan", self._iterative_scan, True),
            )
        )
        distance = ChunkRow.embedding.cosine_distance(vector).label("distance")
        nearest = (
            _joined(select(ChunkRow.id, distance))
            .where(
                _visible(ctx, filters),
                ChunkRow.embedding_model == space.model,
                ChunkRow.embedding_dimensions == space.dimensions,
            )
            # By the label, so the vector is bound once; PostgreSQL resolves it
            # to the same sort key, which is what the HNSW index scan matches.
            .order_by(distance)
            .limit(limit)
            .cte("nearest")
            .prefix_with("MATERIALIZED", dialect="postgresql")
        )
        rows = await self._session.execute(
            select(nearest.c.id, nearest.c.distance).order_by(
                literal_column("distance"), nearest.c.id
            )
        )
        return [
            Candidate(chunk_id=chunk_id, score=1.0 - float(distance)) for chunk_id, distance in rows
        ]

    async def load_results(
        self,
        ctx: AccessContext,
        chunk_ids: Collection[uuid.UUID],
        *,
        filters: SearchFilters,
    ) -> Mapping[uuid.UUID, ChunkRecord]:
        if not chunk_ids:
            return {}
        rows = await self._session.execute(
            _joined(
                select(
                    ChunkRow.id,
                    ChunkRow.workspace_id,
                    ChunkRow.document_id,
                    DocumentRow.title,
                    DocumentRow.updated_at.label("document_updated_at"),
                    VersionRow.content_type,
                    VersionRow.id.label("version_id"),
                    VersionRow.version_number,
                    ChunkRow.ordinal,
                    ChunkRow.content,
                    ChunkRow.heading_path,
                    ChunkRow.page_from,
                    ChunkRow.page_to,
                    ChunkRow.char_start,
                    ChunkRow.char_end,
                )
            ).where(_visible(ctx, filters), ChunkRow.id.in_(sorted(chunk_ids)))
        )
        return {
            row.id: ChunkRecord(
                chunk_id=row.id,
                workspace_id=row.workspace_id,
                document_id=row.document_id,
                document_title=row.title,
                content_type=row.content_type,
                document_updated_at=row.document_updated_at,
                version_id=row.version_id,
                version_number=row.version_number,
                ordinal=row.ordinal,
                content=row.content,
                heading_path=row.heading_path,
                page_from=row.page_from,
                page_to=row.page_to,
                char_start=row.char_start,
                char_end=row.char_end,
            )
            for row in rows
        }
