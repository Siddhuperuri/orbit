"""Embedding spaces, vector validation, and request batching (ADR-0020).

A vector means something only relative to the model that produced it. Two
vectors from different models -- or from one model truncated to different
dimensions -- are not comparable, and a cosine distance between them is a
number with no meaning that nothing downstream can detect. The **embedding
space** is therefore a first-class value here rather than a loose model string:
every stored vector records the space it lives in, every query states the
space it searches, and the two are compared exactly.

Everything in this module is pure -- no I/O, no clock, no provider.
"""

from __future__ import annotations

import hashlib
import math
from array import array
from collections.abc import Sequence
from dataclasses import dataclass

from orbit.domain.errors import AIProviderResponseInvalidError, ConfigurationError

#: Width of `chunks.embedding_model`.
MAX_MODEL_ID_LENGTH = 128

#: pgvector's HNSW index accepts at most 2,000 dimensions for the `vector`
#: type. A space wider than this could be stored but never indexed, so every
#: dense query would be a sequential scan -- refused at configuration time
#: rather than discovered in production latency.
MAX_INDEXABLE_DIMENSIONS = 2000


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    """The vector space a model produces: *which* model, at *what* width.

    Both halves are part of identity. `text-embedding-3-small` truncated to
    512 dimensions is a different space from the same model at 1536, even
    though the model id is identical.
    """

    model: str
    dimensions: int

    def __post_init__(self) -> None:
        if not self.model.strip():
            msg = "An embedding space needs a model id."
            raise ValueError(msg)
        if len(self.model) > MAX_MODEL_ID_LENGTH:
            msg = f"Embedding model ids are limited to {MAX_MODEL_ID_LENGTH} characters."
            raise ValueError(msg)
        if not 1 <= self.dimensions <= MAX_INDEXABLE_DIMENSIONS:
            msg = f"Embedding dimensions must be between 1 and {MAX_INDEXABLE_DIMENSIONS}."
            raise ValueError(msg)

    @property
    def key(self) -> str:
        """Human-readable identity for logs and reports."""
        return f"{self.model}@{self.dimensions}"


def build_embedding_input(heading_path: str | None, text: str) -> str:
    """Exactly the string the provider embeds for a chunk.

    The heading path is prepended because it often carries the discriminating
    term the body only refers to (ADR-0013). This is the single definition:
    the pipeline, the re-index, and migration 0004's SQL backfill must agree
    with it byte for byte, or reuse-by-hash silently stops matching.
    """
    return f"{heading_path}\n\n{text}" if heading_path else text


def embedding_input_sha256(heading_path: str | None, text: str) -> str:
    return hashlib.sha256(build_embedding_input(heading_path, text).encode("utf-8")).hexdigest()


def to_indexable_vector(vector: Sequence[float], space: EmbeddingSpace) -> array[float]:
    """Validate one provider output and convert it to compact float32.

    Refused:

    * **wrong length** -- a configuration defect (the provider and the schema
      disagree about the space). Raised as `ConfigurationError`: retrying the
      same call fails the same way, so it must not burn the retry budget.
    * **NaN, infinity, or a zero vector** -- pgvector rejects NaN at insert,
      far from its cause, and cosine distance to a zero vector is undefined,
      so the chunk would silently never be retrieved. Treated as a malformed
      response and retried: it is a provider fault, not the document's.

    Conversion happens *before* the finiteness check, so a float64 value
    that overflows float32 is caught too.
    """
    if len(vector) != space.dimensions:
        msg = "The embedding provider returned a vector of the wrong size."
        raise ConfigurationError(msg, expected=space.dimensions, received=len(vector))
    compact = array("f", vector)
    # One C-level pass: `hypot` is NaN if any component is NaN and infinite if
    # any is infinite, so this single number answers all three questions.
    norm = math.hypot(*compact)
    if not math.isfinite(norm) or norm == 0.0:
        msg = "The embedding provider returned a vector that cannot be indexed."
        raise AIProviderResponseInvalidError(msg, norm=repr(norm))
    return compact


def to_indexable_vectors(
    vectors: Sequence[Sequence[float]], *, expected: int, space: EmbeddingSpace
) -> list[array[float]]:
    """Validate a whole batch; the count is checked before any vector.

    A short or long response cannot be paired with its inputs safely -- a
    positional zip over a short list would attach every later vector to the
    wrong chunk -- so it is rejected outright.
    """
    if len(vectors) != expected:
        msg = "The embedding provider returned the wrong number of vectors."
        raise AIProviderResponseInvalidError(msg, expected=expected, received=len(vectors))
    return [to_indexable_vector(vector, space) for vector in vectors]


def plan_batches(token_counts: Sequence[int], *, max_items: int, max_tokens: int) -> list[range]:
    """Split inputs into contiguous request batches, in order.

    A batch closes when adding the next input would exceed either bound. An
    input that alone exceeds `max_tokens` is sent on its own rather than
    dropped: the provider is the authority on what it accepts, and it will
    say so with a classified error.
    """
    if max_items < 1 or max_tokens < 1:
        msg = "Batch bounds must be positive."
        raise ValueError(msg)
    batches: list[range] = []
    start = 0
    tokens = 0
    for index, count in enumerate(token_counts):
        size = index - start
        if size and (size >= max_items or tokens + count > max_tokens):
            batches.append(range(start, index))
            start, tokens = index, 0
        tokens += count
    if start < len(token_counts):
        batches.append(range(start, len(token_counts)))
    return batches
