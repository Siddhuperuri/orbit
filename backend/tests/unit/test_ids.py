"""ULID generation.

The sortability property is the entire reason ULIDs were chosen over UUIDv4, so
it is tested directly rather than assumed from the implementation.
"""

from __future__ import annotations

import time

from orbit.core.ids import ULID_LENGTH, is_ulid, new_ulid


def test_has_the_specified_length() -> None:
    assert len(new_ulid()) == ULID_LENGTH == 26


def test_uses_crockford_base32_only() -> None:
    # I, L, O, and U are excluded so a hand-transcribed id cannot be confused
    # with 1 or 0.
    excluded = set("ILOU")
    for _ in range(200):
        assert not (set(new_ulid()) & excluded)


def test_identifiers_are_unique() -> None:
    generated = {new_ulid() for _ in range(10_000)}
    assert len(generated) == 10_000


def test_lexicographic_order_follows_time_order() -> None:
    """The property that makes ULIDs worth implementing.

    Sorting log records by request id must yield chronological order.
    """
    first = new_ulid()
    # ULID timestamp resolution is one millisecond; without a pause, two ids
    # generated in the same millisecond order by their random component.
    time.sleep(0.005)
    second = new_ulid()
    assert first < second


def test_same_millisecond_ids_remain_valid_and_distinct() -> None:
    batch = [new_ulid() for _ in range(100)]
    assert len(set(batch)) == 100
    assert all(is_ulid(value) for value in batch)


class TestValidation:
    def test_accepts_generated_values(self) -> None:
        assert is_ulid(new_ulid())

    def test_rejects_wrong_length(self) -> None:
        assert not is_ulid("")
        assert not is_ulid("01JB2X8N4K7QF3TVWZ9M5PDCR")
        assert not is_ulid("01JB2X8N4K7QF3TVWZ9M5PDCRAA")

    def test_rejects_excluded_and_invalid_characters(self) -> None:
        assert not is_ulid("I" * 26)
        assert not is_ulid("-" * 26)
        assert not is_ulid("01jb2x8n4k7qf3tvwz9m5pdcra")

    def test_rejects_injection_attempts(self) -> None:
        # An unvalidated X-Request-ID header would let a caller inject newlines
        # into every log record for that request.
        assert not is_ulid("01JB2X8N4K\n7QF3TVWZ9M5PD")
        assert not is_ulid("../../etc/passwd")
