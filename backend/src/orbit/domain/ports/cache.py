"""Caching ports.

Two rules govern every cache in ORBIT, and both are encoded in the protocols
below rather than left to the discipline of each call site.

**1. A cache is an optimisation, never a source of truth.** Redis is
disposable (docs/architecture/system.md). Every method here is therefore
declared *non-raising*: a backend outage returns a miss, never an exception.
The caller's code path for "not cached" is the same code path that runs during
a Redis outage, so the outage is exercised by every cold start rather than
only in production. See ADR-0024.

**2. Nothing whose correctness depends on authorization is cached.** There is
deliberately no `Cache.get(key)` that a use case could reach for to memoise a
membership lookup or a result page. The only caching port is
:class:`QueryVectorCache`, which holds the output of a *pure function* of text
-- and even that is keyed by workspace, because the timing difference between
a hit and a miss would otherwise reveal that some other tenant had searched
for the same string.

The authorization argument is worth stating in full, because "just add a short
TTL" is the obvious and wrong answer. Revocation is the problem: a cached
membership means a user removed from a workspace keeps reading it until the
entry expires. A TTL short enough to make that acceptable is short enough that
the cache barely hits, so the feature costs a correctness risk and buys
nothing. Invalidation-on-write does not rescue it either -- the write that must
invalidate is a *role change in another process*, and a missed invalidation
fails open, silently, in the direction of granting access. ORBIT therefore
resolves access from PostgreSQL on every request, and pays one indexed primary
key lookup for the privilege.
"""

from __future__ import annotations

import uuid
from array import array
from collections.abc import Sequence
from typing import Protocol

from orbit.domain.embeddings import EmbeddingSpace


class QueryVectorCache(Protocol):
    """Memoises the embedding of a *search query*.

    A query vector is a deterministic function of (text, embedding space), so
    a hit is indistinguishable from a miss in its result -- unlike a cached
    document list, which can be stale, or a cached permission, which can be
    wrong. What it saves is a paid round trip to an external provider on the
    latency path of every search and every question.

    It is scoped to one workspace even though the value is tenant-independent.
    That is not confusion about what the value contains; it is a side-channel
    control. A process-wide cache would let a member of workspace A measure
    whether anyone in workspace B had recently searched a given phrase, purely
    from response latency. Scoping the key removes the channel, and the cost
    is only a lower hit rate on phrases two tenants happen to share.

    Neither method raises. A backend failure is a miss and a no-op write.
    """

    async def get(
        self, *, workspace_id: uuid.UUID, space: EmbeddingSpace, text: str
    ) -> array[float] | None:
        """The cached vector for this exact text, or `None` on a miss.

        A stored vector whose width disagrees with `space` is treated as a
        miss: it was written by a different model, or before a re-index, and
        using it would put a meaningless distance into a ranking.
        """
        ...

    async def put(
        self,
        *,
        workspace_id: uuid.UUID,
        space: EmbeddingSpace,
        text: str,
        vector: Sequence[float],
    ) -> None:
        """Record `vector` for this text. Best-effort; failures are swallowed."""
        ...
