"""Password hashing.

Argon2id is deliberately slow, so these tests use short passwords and avoid
hashing more times than each assertion needs -- the suite's speed matters more
here than anywhere else in the unit tree.
"""

from __future__ import annotations

from orbit.core.security import hash_password, needs_rehash, verify_password


def test_hash_is_not_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2id$")


def test_verifies_the_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_rejects_the_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password entirely", hashed) is False


def test_two_hashes_of_the_same_password_differ() -> None:
    # Argon2id salts automatically; identical hashes for identical passwords
    # would mean the salt was reused, which defeats its purpose.
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second


def test_a_malformed_hash_is_treated_as_a_mismatch_not_a_crash() -> None:
    """Data corruption must fail closed, not raise past the auth boundary."""
    assert verify_password("anything", "not-a-real-argon2-hash") is False


def test_an_empty_hash_is_treated_as_a_mismatch() -> None:
    assert verify_password("anything", "") is False


def test_fresh_hash_does_not_need_rehashing() -> None:
    assert needs_rehash(hash_password("correct horse battery staple")) is False
