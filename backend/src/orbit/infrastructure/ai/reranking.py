"""Reranker implementations.

Only the pass-through exists. It is the default by decision, not by omission
(ADR-0007): a cross-encoder is introduced when the retrieval benchmark shows
fusion alone is insufficient, so its benefit is measured against a baseline.
"""

from __future__ import annotations

from collections.abc import Sequence

from orbit.domain.retrieval import SearchResult


class PassthroughReranker:
    """Keeps the fused order; only applies `top_k`."""

    @property
    def name(self) -> str:
        return "passthrough"

    async def rerank(
        self, query: str, results: Sequence[SearchResult], *, top_k: int
    ) -> Sequence[SearchResult]:
        del query
        return results[:top_k]
