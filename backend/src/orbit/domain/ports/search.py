"""The search port: two independent retrievers, and a guarded materialiser.

Retrievers return **identifiers and scores only**. Text, titles, and locations
are loaded afterwards, for the fused winners alone, by `load_results` -- a
second query that re-applies the caller's workspace and visibility rules. So
authorization is enforced twice, and a result is only ever exposed through
the second check.

Every method takes the caller's `AccessContext`, and every implementation
puts `workspace_id` *inside* its query (ADR-0004). Similarity is never
computed across tenants and then filtered: a chunk from another workspace
cannot surface through semantic closeness, because it is never a candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Protocol

from orbit.domain.access import AccessContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.retrieval import Candidate, ChunkRecord, LexicalMatch, SearchFilters


class SearchRepository(Protocol):
    async def lexical_candidates(
        self,
        ctx: AccessContext,
        text: str,
        *,
        match: LexicalMatch,
        filters: SearchFilters,
        limit: int,
    ) -> Sequence[Candidate]:
        """Full-text matches, best first, scored by `ts_rank_cd`."""
        ...

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
        """Nearest chunks in `space`, best first, scored by cosine similarity."""
        ...

    async def load_results(
        self,
        ctx: AccessContext,
        chunk_ids: Collection[uuid.UUID],
        *,
        filters: SearchFilters,
    ) -> Mapping[uuid.UUID, ChunkRecord]:
        """Materialise chunks for display.

        Returns only chunks that are, *now*, in the caller's workspace, within
        `filters`, and searchable (current READY version, undeleted document).
        An id that fails any of those is absent from the result.
        """
        ...
