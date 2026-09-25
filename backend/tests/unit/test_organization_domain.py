"""Folder and tag rules, and the places the code and the schema must agree."""

from __future__ import annotations

from typing import get_args

import pytest
from sqlalchemy import CheckConstraint

from orbit.api.v1.schemas.organization import TagColor
from orbit.domain.documents import MAX_DOCUMENT_TITLE_LENGTH
from orbit.domain.errors import ValidationError
from orbit.domain.organization import (
    DEFAULT_TAG_COLOR,
    MAX_FOLDER_DEPTH,
    MAX_FOLDER_NAME_LENGTH,
    MAX_TAG_NAME_LENGTH,
    TAG_COLORS,
    normalize_folder_name,
    normalize_tag_name,
    require_tag_color,
)
from orbit.infrastructure.db.models import Folder
from orbit.infrastructure.db.models.content import MAX_TITLE_LENGTH


class TestNormalizeName:
    def test_surrounding_and_repeated_whitespace_is_collapsed(self) -> None:
        assert normalize_folder_name("  Q3   plan \t 2026 ") == "Q3 plan 2026"

    def test_two_spellings_of_the_same_character_become_one_name(self) -> None:
        composed = "café"
        decomposed = "café"
        assert composed != decomposed
        assert normalize_tag_name(composed) == normalize_tag_name(decomposed)

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
    def test_blank_is_refused(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="blank"):
            normalize_folder_name(blank)

    def test_control_characters_are_refused_not_silently_stripped(self) -> None:
        with pytest.raises(ValidationError, match="control"):
            normalize_folder_name("bad\x00name")

    def test_the_length_limit_applies_after_normalisation(self) -> None:
        assert normalize_tag_name("a " * 30 + "b") == "a " * 30 + "b"  # 61 chars
        with pytest.raises(ValidationError, match="exceed"):
            normalize_tag_name("x" * (MAX_TAG_NAME_LENGTH + 1))

    def test_emoji_joiners_are_not_mistaken_for_control_characters(self) -> None:
        """U+200D (zero-width joiner) is a format character (Cf), not a control (Cc);
        refusing it would make family and flag emoji impossible."""
        assert normalize_tag_name("\U0001f469‍\U0001f4bb") == "\U0001f469‍\U0001f4bb"


class TestTagColor:
    def test_none_means_the_default(self) -> None:
        assert require_tag_color(None) == DEFAULT_TAG_COLOR

    @pytest.mark.parametrize("color", TAG_COLORS)
    def test_every_palette_colour_is_accepted(self, color: str) -> None:
        assert require_tag_color(color) == color

    @pytest.mark.parametrize("color", ["red", "#ff0000", "", "NEUTRAL"])
    def test_anything_else_is_refused(self, color: str) -> None:
        with pytest.raises(ValidationError, match="one of"):
            require_tag_color(color)

    def test_the_api_enum_and_the_domain_palette_cannot_drift(self) -> None:
        assert tuple(get_args(TagColor)) == TAG_COLORS


class TestConstantsMatchTheSchema:
    """A limit that exists in both the code and the database is only safe if a test
    fails when one moves without the other."""

    def test_folder_depth_matches_the_check_constraint(self) -> None:
        bounds = [
            str(c.sqltext)
            for c in Folder.__table_args__
            if isinstance(c, CheckConstraint) and "depth <=" in str(c.sqltext)
        ]
        assert bounds, "the depth check constraint is missing from the model"
        assert f"depth <= {MAX_FOLDER_DEPTH}" in bounds[0]

    def test_document_title_limit_matches_the_column(self) -> None:
        assert MAX_DOCUMENT_TITLE_LENGTH == MAX_TITLE_LENGTH

    def test_folder_names_fit_their_column(self) -> None:
        assert MAX_FOLDER_NAME_LENGTH <= MAX_TITLE_LENGTH
