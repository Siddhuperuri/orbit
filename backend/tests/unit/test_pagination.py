"""Keyset cursor signing and pagination limits, independent of the database."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest

from orbit.domain.errors import BadRequestError
from orbit.domain.models.entities import ProcessingOutcome
from orbit.domain.models.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Cursor,
    clamp_limit,
)

_SECRET = "test-cursor-secret"


class TestCursor:
    def test_round_trips(self) -> None:
        original = Cursor(created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.uuid4())
        decoded = Cursor.decode(original.encode(_SECRET), _SECRET)
        assert decoded == original

    def test_round_trips_when_the_signature_contains_the_separator_byte(self) -> None:
        """Regression: the decoder once split on the *last* "." in the decoded
        bytes, and a raw HMAC byte of 0x2E moved that split into the signature
        -- rejecting ~6% of genuine cursors as forged. Found by the integration
        suite's pagination walk, which issues enough cursors to hit it."""
        hits = 0
        for index in range(2000):
            original = Cursor(
                created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.UUID(int=index)
            )
            encoded = original.encode(_SECRET)
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            hits += b"." in raw[-16:]
            assert Cursor.decode(encoded, _SECRET) == original
        # Guards the regression test itself: it must actually exercise the case.
        assert hits > 0

    def test_two_encodings_of_the_same_cursor_are_identical(self) -> None:
        """Deterministic encoding: a client comparing cursors byte-for-byte
        (e.g. to detect "no change since last fetch") gets a stable answer."""
        cursor = Cursor(created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.uuid4())
        assert cursor.encode(_SECRET) == cursor.encode(_SECRET)

    def test_rejects_a_tampered_payload(self) -> None:
        cursor = Cursor(created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.uuid4())
        encoded = cursor.encode(_SECRET)
        tampered = encoded[:-4] + ("A" * 4 if not encoded.endswith("AAAA") else "BBBB")

        with pytest.raises(BadRequestError):
            Cursor.decode(tampered, _SECRET)

    def test_rejects_a_cursor_signed_with_a_different_secret(self) -> None:
        """The property that makes a cursor safe to hand to a client: it
        cannot be forged to page outside the filter it was issued under."""
        cursor = Cursor(created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.uuid4())
        encoded = cursor.encode(_SECRET)

        with pytest.raises(BadRequestError):
            Cursor.decode(encoded, "a-different-secret")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(BadRequestError):
            Cursor.decode("not-a-real-cursor!!", _SECRET)

    def test_rejects_an_empty_string(self) -> None:
        with pytest.raises(BadRequestError):
            Cursor.decode("", _SECRET)

    def test_error_message_does_not_distinguish_malformed_from_forged(self) -> None:
        """Telling a caller *which* half of a forgery attempt failed would
        hand back a debugging oracle for free."""
        cursor = Cursor(created_at=datetime(2026, 1, 1, tzinfo=UTC), row_id=uuid.uuid4())
        wrong_secret_error = None
        garbage_error = None
        try:
            Cursor.decode(cursor.encode(_SECRET), "wrong")
        except BadRequestError as exc:
            wrong_secret_error = str(exc)
        try:
            Cursor.decode("garbage", _SECRET)
        except BadRequestError as exc:
            garbage_error = str(exc)
        assert wrong_secret_error == garbage_error


class TestClampLimit:
    def test_none_yields_the_default(self) -> None:
        assert clamp_limit(None) == DEFAULT_PAGE_SIZE

    def test_a_reasonable_value_passes_through(self) -> None:
        assert clamp_limit(10) == 10

    def test_a_value_over_the_ceiling_is_clamped_not_rejected(self) -> None:
        """A generous client asking for more than the ceiling gets the
        ceiling, not an error -- there is no reason to fail the request over
        this particular mistake."""
        assert clamp_limit(MAX_PAGE_SIZE + 500) == MAX_PAGE_SIZE

    def test_a_value_at_the_ceiling_is_unchanged(self) -> None:
        assert clamp_limit(MAX_PAGE_SIZE) == MAX_PAGE_SIZE

    @pytest.mark.parametrize("value", [0, -1, -1000])
    def test_a_non_positive_value_is_rejected(self, value: int) -> None:
        """Unlike an oversized limit, a non-positive one has no sensible
        interpretation to clamp to, so it is a client error."""
        with pytest.raises(BadRequestError):
            clamp_limit(value)


class TestProcessingOutcomeGuardsMirrorTheDatabase:
    """These duplicate two check constraints from
    infrastructure/db/models/content.py deliberately: catching the mistake in
    Python before a round trip is strictly better, as long as the two rules
    are kept identical -- which is what these tests are for."""

    def test_ready_requires_at_least_one_chunk(self) -> None:
        with pytest.raises(ValueError, match="at least one chunk"):
            ProcessingOutcome.ready(chunk_count=0)

    def test_failed_requires_a_code_and_reason(self) -> None:
        with pytest.raises(ValueError, match="failure_code"):
            ProcessingOutcome(status=ProcessingOutcome.ready(chunk_count=1).status.FAILED)
