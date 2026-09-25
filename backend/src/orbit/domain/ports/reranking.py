"""The optional reranking stage between retrieval and context construction.

A reranker reorders -- and may shorten -- the fused candidate list. It may not
add candidates: whatever it returns must be a subset of what it was given,
because only retrieved chunks have passed the workspace and visibility checks.
The answering use case enforces that rather than trusting it.

The default is a pass-through (ADR-0007): a cross-encoder is added when a
benchmark shows fusion alone is insufficient, so its benefit is measured
against a known baseline rather than assumed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from orbit.domain.retrieval import SearchResult


class Reranker(Protocol):
    @property
    def name(self) -> str:
        """Recorded with each answer, so a quality change is attributable."""
        ...

    async def rerank(
        self, query: str, results: Sequence[SearchResult], *, top_k: int
    ) -> Sequence[SearchResult]:
        """At most `top_k` of `results`, best first."""
        ...
