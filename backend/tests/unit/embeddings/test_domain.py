"""The pure embedding domain: spaces, input identity, vector validation, and
request batching."""

from __future__ import annotations

import hashlib
import math
from array import array

import pytest

from orbit.core import config
from orbit.domain import embeddings
from orbit.domain.embeddings import (
    EmbeddingSpace,
    build_embedding_input,
    embedding_input_sha256,
    plan_batches,
    to_indexable_vector,
    to_indexable_vectors,
)
from orbit.domain.errors import AIProviderResponseInvalidError, ConfigurationError
from orbit.domain.processing.content import ChunkDraft

SPACE = EmbeddingSpace(model="model-a", dimensions=4)


class TestEmbeddingSpace:
    def test_model_and_width_are_both_identity(self) -> None:
        assert EmbeddingSpace("m", 512) != EmbeddingSpace("m", 1536)
        assert EmbeddingSpace("m", 512) != EmbeddingSpace("n", 512)
        assert EmbeddingSpace("m", 512) == EmbeddingSpace("m", 512)
        assert EmbeddingSpace("m", 512).key == "m@512"

    @pytest.mark.parametrize(
        ("model", "dimensions"),
        [("", 8), ("   ", 8), ("m" * 129, 8), ("m", 0), ("m", 2001)],
    )
    def test_invalid_spaces_are_unrepresentable(self, model: str, dimensions: int) -> None:
        with pytest.raises(ValueError):
            EmbeddingSpace(model=model, dimensions=dimensions)

    def test_configuration_limits_mirror_the_domain(self) -> None:
        # `core` cannot import `domain`, so the limits are copied; this keeps
        # the copies honest.
        assert config.MAX_EMBEDDING_DIMENSIONS == embeddings.MAX_INDEXABLE_DIMENSIONS
        assert config.MAX_EMBEDDING_MODEL_ID_LENGTH == embeddings.MAX_MODEL_ID_LENGTH


class TestEmbeddingInput:
    def test_the_heading_path_is_prepended_only_when_present(self) -> None:
        assert build_embedding_input("Security > Tokens", "Rotate.") == (
            "Security > Tokens\n\nRotate."
        )
        assert build_embedding_input(None, "Rotate.") == "Rotate."
        assert build_embedding_input("", "Rotate.") == "Rotate."

    def test_the_hash_identifies_exactly_what_is_embedded(self) -> None:
        expected = hashlib.sha256(b"H\n\nbody").hexdigest()
        assert embedding_input_sha256("H", "body") == expected
        assert embedding_input_sha256(None, "body") != embedding_input_sha256("H", "body")

    def test_a_draft_hashes_its_embedding_input_not_its_stored_text(self) -> None:
        draft = ChunkDraft(
            ordinal=0,
            text="Rotate refresh tokens.",
            token_count=4,
            char_start=0,
            char_end=22,
            page_start=None,
            page_end=None,
            heading_path=("Security", "Tokens"),
        )
        assert draft.embedding_input_sha256 == embedding_input_sha256(
            "Security > Tokens", "Rotate refresh tokens."
        )
        assert draft.embedding_input_sha256 != draft.content_sha256


class TestVectorValidation:
    def test_a_valid_vector_becomes_compact_float32(self) -> None:
        vector = to_indexable_vector([0.5, 0.5, 0.5, 0.5], SPACE)
        assert isinstance(vector, array)
        assert vector.typecode == "f"
        assert list(vector) == [0.5, 0.5, 0.5, 0.5]

    @pytest.mark.parametrize("length", [3, 5, 0])
    def test_the_wrong_width_is_a_configuration_defect_not_a_retry(self, length: int) -> None:
        with pytest.raises(ConfigurationError):
            to_indexable_vector([0.1] * length, SPACE)

    @pytest.mark.parametrize(
        "vector",
        [
            [math.nan, 0.0, 0.0, 1.0],
            [math.inf, 0.0, 0.0, 1.0],
            [0.0, 0.0, 0.0, 0.0],
            # Finite as float64, infinite once stored as float32.
            [1e39, 0.0, 0.0, 0.0],
        ],
        ids=["nan", "inf", "zero", "float32-overflow"],
    )
    def test_unindexable_values_are_an_invalid_provider_response(self, vector: list[float]) -> None:
        with pytest.raises(AIProviderResponseInvalidError):
            to_indexable_vector(vector, SPACE)

    @pytest.mark.parametrize("returned", [1, 3])
    def test_a_batch_with_the_wrong_count_is_never_paired_positionally(self, returned: int) -> None:
        with pytest.raises(AIProviderResponseInvalidError):
            to_indexable_vectors([[1.0, 0, 0, 0]] * returned, expected=2, space=SPACE)


class TestBatchPlanning:
    def test_batches_close_at_the_item_bound(self) -> None:
        assert plan_batches([1] * 5, max_items=2, max_tokens=100) == [
            range(0, 2),
            range(2, 4),
            range(4, 5),
        ]

    def test_batches_close_at_the_token_bound(self) -> None:
        assert plan_batches([40, 40, 40, 10], max_items=10, max_tokens=80) == [
            range(0, 2),
            range(2, 4),
        ]

    def test_an_oversized_input_is_sent_alone_rather_than_dropped(self) -> None:
        assert plan_batches([10, 500, 10], max_items=10, max_tokens=100) == [
            range(0, 1),
            range(1, 2),
            range(2, 3),
        ]

    def test_every_input_appears_exactly_once_in_order(self) -> None:
        counts = [((i * 37) % 90) + 1 for i in range(1000)]
        batches = plan_batches(counts, max_items=17, max_tokens=400)
        assert [i for batch in batches for i in batch] == list(range(1000))
        for batch in batches:
            assert len(batch) <= 17
            assert len(batch) == 1 or sum(counts[i] for i in batch) <= 400

    def test_nothing_to_send_is_no_batches(self) -> None:
        assert plan_batches([], max_items=1, max_tokens=1) == []

    def test_bounds_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            plan_batches([1], max_items=0, max_tokens=1)
