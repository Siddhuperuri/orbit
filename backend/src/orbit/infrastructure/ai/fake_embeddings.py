"""Deterministic, offline embedding provider (ADR-0007).

The default provider, and a supported operating mode rather than a test stub:
the whole pipeline -- upload to READY, and dense search -- runs under it with
no credentials and no network.

Vectors are *feature-hashed* word counts, L2-normalized. That makes them
deterministic (identical text, identical vector, on every machine and every
run) and gives them one honest property beyond determinism: texts that share
words have a higher cosine similarity than texts that do not. That is lexical
overlap, not meaning -- retrieval quality under this provider is not a
benchmark of anything (ADR-0007).

**Its model id is its own and is never taken from configuration.** A fake
vector recorded as `text-embedding-3-small` would be accepted as that model's
output once the real provider is switched on, and dense retrieval would
silently compare real query vectors against hashed word counts. Under its own
id, the switch shows up as a stale space and the re-index picks it up
(docs/database/embeddings.md).
"""

from __future__ import annotations

import hashlib
import math
import re
from array import array
from collections.abc import Sequence

from orbit.domain.embeddings import EmbeddingSpace

_WORD = re.compile(r"\w+", re.UNICODE)

#: Bump the suffix if the hashing scheme ever changes: vectors from the old
#: scheme then read as a different space rather than as comparable ones.
FAKE_EMBEDDING_MODEL = "orbit-fake-embedding-v1"


class FakeEmbeddingProvider:
    def __init__(
        self,
        *,
        dimensions: int,
        model_id: str = FAKE_EMBEDDING_MODEL,
        max_batch_size: int = 256,
        max_batch_tokens: int = 1_000_000,
    ) -> None:
        # `EmbeddingSpace` validates both halves.
        self._space = EmbeddingSpace(model=model_id, dimensions=dimensions)
        self._max_batch_size = max_batch_size
        self._max_batch_tokens = max_batch_tokens

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def max_batch_size(self) -> int:
        return self._max_batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self._max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [self.vector(text) for text in texts]

    async def embed_query(self, text: str) -> Sequence[float]:
        # Symmetric: this scheme has no query/document distinction to make.
        return self.vector(text)

    def vector(self, text: str) -> array[float]:
        """Synchronous access for tests and benchmarks that need a vector
        without an event loop."""
        dimensions = self._space.dimensions
        # Seeded with the space's identity, so the same word lands on different
        # dimensions under a different fake model id -- exactly as vectors from
        # two different real models would not line up.
        salt = _salt(self._space.model.encode("utf-8"))
        vector = array("f", bytes(4 * dimensions))
        words = _WORD.findall(text.casefold()) or [text]
        for word in words:
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8, salt=salt).digest()
            value = int.from_bytes(digest, "big")
            index = value % dimensions
            # The sign bit decorrelates collisions: two words hashing to the
            # same dimension cancel as often as they reinforce.
            vector[index] += 1.0 if (value >> 63) & 1 else -1.0
        norm = math.hypot(*vector)
        if norm == 0:
            # Every word cancelled out. Any fixed unit vector is a valid,
            # deterministic answer; a zero vector is not (cosine is undefined).
            vector[0] = 1.0
            return vector
        for index, component in enumerate(vector):
            if component:
                vector[index] = component / norm
        return vector


def _salt(model: bytes) -> bytes:
    """blake2b salts are at most 16 bytes; derive one from any model id.

    The default model keeps an empty salt, so vectors under the default id are
    byte-identical to those produced before salting existed.
    """
    if model == FAKE_EMBEDDING_MODEL.encode("utf-8"):
        return b""
    return hashlib.blake2b(model, digest_size=16).digest()
