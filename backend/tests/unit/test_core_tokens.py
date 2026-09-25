"""Access token issuance/verification, and refresh token hashing."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from orbit.core.clock import FixedClock
from orbit.core.tokens import (
    InvalidTokenError,
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    issue_refresh_token,
)

_SECRET = "test-secret-key-not-for-any-real-environment-0123456789"


class TestAccessToken:
    def test_round_trips_the_user_id_and_epoch(self) -> None:
        user_id = uuid.uuid4()
        clock = FixedClock()
        token = issue_access_token(
            user_id=user_id,
            token_epoch=3,
            secret_key=_SECRET,
            ttl=timedelta(minutes=15),
            clock=clock,
        )

        claims = decode_access_token(token, secret_key=_SECRET)
        assert claims.user_id == user_id
        assert claims.token_epoch == 3

    def test_expires_after_its_ttl(self) -> None:
        clock = FixedClock()
        token = issue_access_token(
            user_id=uuid.uuid4(),
            token_epoch=0,
            secret_key=_SECRET,
            ttl=timedelta(seconds=1),
            clock=clock,
        )
        clock.advance(timedelta(seconds=2))

        # Decoding with the SAME fake clock is what makes this deterministic:
        # PyJWT's own expiry check runs against real wall time and has no
        # hook to override, so `verify_exp` is disabled and expiry is
        # re-checked here against whichever clock the caller injects.
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, secret_key=_SECRET, clock=clock)

    def test_is_valid_up_to_the_instant_before_expiry(self) -> None:
        clock = FixedClock()
        token = issue_access_token(
            user_id=uuid.uuid4(),
            token_epoch=0,
            secret_key=_SECRET,
            ttl=timedelta(seconds=60),
            clock=clock,
        )
        clock.advance(timedelta(seconds=59))
        decode_access_token(token, secret_key=_SECRET, clock=clock)  # does not raise

    def test_is_expired_at_the_exact_expiry_instant(self) -> None:
        """Boundary case: `expires_at` is exclusive."""
        clock = FixedClock()
        token = issue_access_token(
            user_id=uuid.uuid4(),
            token_epoch=0,
            secret_key=_SECRET,
            ttl=timedelta(seconds=60),
            clock=clock,
        )
        clock.advance(timedelta(seconds=60))
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, secret_key=_SECRET, clock=clock)

    def test_rejects_a_token_signed_with_a_different_key(self) -> None:
        token = issue_access_token(
            user_id=uuid.uuid4(),
            token_epoch=0,
            secret_key=_SECRET,
            ttl=timedelta(minutes=15),
            clock=FixedClock(),
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, secret_key="a-completely-different-signing-key")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(InvalidTokenError):
            decode_access_token("not.a.jwt", secret_key=_SECRET)

    def test_rejects_an_algorithm_switch_attack(self) -> None:
        """A token whose header claims `alg: none` must not verify.

        This is the classic JWT downgrade attack: if the decoder ever accepted
        the algorithm named in the token itself rather than pinning HS256, an
        attacker could mint an unsigned token and have it accepted as valid.
        """
        forged = jwt.encode({"sub": str(uuid.uuid4()), "epoch": 0}, key="", algorithm="none")
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged, secret_key=_SECRET)

    def test_rejects_a_token_missing_required_claims(self) -> None:
        malformed = jwt.encode({"sub": str(uuid.uuid4())}, _SECRET, algorithm="HS256")
        with pytest.raises(InvalidTokenError):
            decode_access_token(malformed, secret_key=_SECRET)


class TestRefreshToken:
    def test_has_sufficient_entropy(self) -> None:
        issued = issue_refresh_token()
        # 32 bytes of urlsafe-base64 encodes to at least 43 characters.
        assert len(issued.plaintext) >= 43

    def test_two_issued_tokens_differ(self) -> None:
        first = issue_refresh_token()
        second = issue_refresh_token()
        assert first.plaintext != second.plaintext

    def test_the_stored_hash_is_not_the_plaintext(self) -> None:
        issued = issue_refresh_token()
        assert issued.sha256_hex != issued.plaintext

    def test_hashing_is_deterministic(self) -> None:
        """The same plaintext must always hash identically, or a legitimate
        refresh would never find its own row by hash lookup."""
        issued = issue_refresh_token()
        assert hash_refresh_token(issued.plaintext) == issued.sha256_hex

    def test_hash_is_a_64_character_hex_digest(self) -> None:
        issued = issue_refresh_token()
        assert len(issued.sha256_hex) == 64
        assert all(c in "0123456789abcdef" for c in issued.sha256_hex)
