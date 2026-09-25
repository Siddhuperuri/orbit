"""Validation utilities shared across request schemas."""

from __future__ import annotations

import pytest

from orbit.core.validation import (
    normalize_email,
    require_non_blank,
    validate_password,
    validate_slug,
)


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "value", ["ada@example.com", "  ada@example.com  ", "a.b+tag@sub.example.co.uk"]
    )
    def test_accepts_plausible_addresses(self, value: str) -> None:
        assert "@" in normalize_email(value)

    def test_trims_incidental_whitespace(self) -> None:
        assert normalize_email("  ada@example.com  ") == "ada@example.com"

    def test_preserves_case(self) -> None:
        """Lookups are case-insensitive at the database index; the address a
        person typed is what correspondence should be sent to."""
        assert normalize_email("Ada@Example.com") == "Ada@Example.com"

    @pytest.mark.parametrize(
        "value", ["", "   ", "not-an-email", "@example.com", "ada@", "ada example.com"]
    )
    def test_rejects_malformed_addresses(self, value: str) -> None:
        with pytest.raises(ValueError, match="valid email"):
            normalize_email(value)

    def test_rejects_an_address_over_the_length_limit(self) -> None:
        with pytest.raises(ValueError, match="valid email"):
            normalize_email("a" * 310 + "@example.com")


class TestRequireNonBlank:
    def test_trims_and_returns(self) -> None:
        assert require_non_blank("  hello  ") == "hello"

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_rejects_blank_after_trimming(self, value: str) -> None:
        """The case a naive `min_length=1` check misses: whitespace passes
        length validation while carrying no real content."""
        with pytest.raises(ValueError, match="cannot be blank"):
            require_non_blank(value)

    def test_rejects_over_the_max_length(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            require_non_blank("x" * 10, max_length=5)

    def test_error_names_the_field(self) -> None:
        with pytest.raises(ValueError, match="title cannot be blank"):
            require_non_blank("", field_name="title")


class TestValidateSlug:
    @pytest.mark.parametrize("value", ["research", "team-42", "a1b2c3", "x-y-z"])
    def test_accepts_the_documented_shape(self, value: str) -> None:
        assert validate_slug(value) == value

    def test_lowercases(self) -> None:
        assert validate_slug("Research") == "research"

    @pytest.mark.parametrize(
        "value",
        [
            "a",  # too short
            "-leading",
            "trailing-",
            "has space",
            "has_underscore",
            "üñî",
            "a" * 65,
        ],
    )
    def test_rejects_shapes_the_database_constraint_also_rejects(self, value: str) -> None:
        """Mirrors ck_workspaces_slug_format exactly, so a client sees the same
        rule the database would otherwise enforce as an opaque 409."""
        with pytest.raises(ValueError):
            validate_slug(value)


class TestValidatePassword:
    def test_accepts_a_sufficiently_long_password(self) -> None:
        value = "a" * 12
        assert validate_password(value) == value

    def test_rejects_a_short_password(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            validate_password("short")

    def test_rejects_an_excessively_long_password(self) -> None:
        """A length ceiling, not a complexity rule (NIST SP 800-63B): the
        point is stopping gigabytes of input from reaching the hasher, not
        forcing a particular character mix."""
        with pytest.raises(ValueError, match="cannot exceed"):
            validate_password("a" * 300)

    def test_does_not_require_a_particular_character_mix(self) -> None:
        # No digit, no symbol, no uppercase -- length alone is sufficient.
        assert validate_password("all lowercase words here") == "all lowercase words here"
