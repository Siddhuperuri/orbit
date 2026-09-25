"""The rules behind listing, editing, and reading documents -- no database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from orbit.domain.documents import (
    MAX_FILTER_TAGS,
    MAX_TEXT_FILTER_LENGTH,
    ArchiveFilter,
    DocumentEdit,
    DocumentListQuery,
    DocumentSort,
    Passage,
    without_overlap,
)
from orbit.domain.errors import BadRequestError, ValidationError
from orbit.domain.models.pagination import Cursor, SortCursor

SECRET = "cursor-test-secret"


def _passage(ordinal: int, start: int, end: int, text: str | None = None, **kw: object) -> Passage:
    return Passage(
        ordinal=ordinal,
        text=text if text is not None else "x" * (end - start),
        char_start=start,
        char_end=end,
        **kw,  # type: ignore[arg-type]
    )


class TestWithoutOverlap:
    """The chunker repeats the previous chunk's trailing sentences at the start of
    the next one. Reading needs them removed; offsets make that exact."""

    def test_passages_that_do_not_overlap_are_untouched(self) -> None:
        passages = [_passage(0, 0, 10, "0123456789"), _passage(1, 12, 20, "abcdefgh")]
        assert without_overlap(passages, preceding_end=None) == tuple(passages)

    def test_the_repeated_prefix_is_removed_exactly(self) -> None:
        # Normalised text: "AAAA BBBB. CCCC DDDD."; the second chunk starts inside the first.
        text = "AAAA BBBB. CCCC DDDD."
        first = _passage(0, 0, 10, text[0:10])
        second = _passage(1, 5, 21, text[5:21])  # "BBBB. CCCC DDDD." repeats "BBBB."

        trimmed = without_overlap([first, second], preceding_end=None)

        assert trimmed[0].text == "AAAA BBBB."
        assert trimmed[1].text == "CCCC DDDD."
        # Reassembled, the trimmed passages read as the original text.
        assert " ".join(p.text for p in trimmed) == text

    def test_leading_whitespace_left_by_the_trim_is_dropped_and_offsets_follow(self) -> None:
        text = "One. Two. Three."
        first = _passage(0, 0, 9, text[0:9])  # "One. Two."
        second = _passage(1, 5, 16, text[5:16])  # "Two. Three."

        trimmed = without_overlap([first, second], preceding_end=None)

        assert trimmed[1].text == "Three."
        # `char_start` now points at where the visible text really begins.
        assert text[trimmed[1].char_start : trimmed[1].char_end] == "Three."

    def test_the_seam_between_two_pages_is_trimmed_using_the_preceding_end(self) -> None:
        text = "Alpha beta. Gamma delta."
        # The page starts mid-document; the previous page's last passage ended at 11.
        head = _passage(4, 6, 24, text[6:24])  # "beta. Gamma delta." repeats "beta."

        trimmed = without_overlap([head], preceding_end=11)

        assert trimmed[0].text == "Gamma delta."

    def test_no_preceding_end_means_nothing_is_trimmed_from_the_first_passage(self) -> None:
        passage = _passage(0, 0, 5, "hello")
        assert without_overlap([passage], preceding_end=None)[0].text == "hello"

    def test_a_passage_wholly_inside_its_predecessor_keeps_its_ordinal_but_has_no_text(
        self,
    ) -> None:
        """Citations name ordinals. A trimmed-away passage must still exist, so a
        deep link to it lands somewhere instead of nowhere."""
        first = _passage(0, 0, 20, "a" * 20)
        inside = _passage(1, 5, 15, "a" * 10)

        trimmed = without_overlap([first, inside], preceding_end=None)

        assert [p.ordinal for p in trimmed] == [0, 1]
        assert trimmed[1].text == ""

    def test_metadata_survives_the_trim(self) -> None:
        first = _passage(0, 0, 10, "0123456789")
        second = _passage(1, 6, 20, "x" * 14, page_from=3, page_to=4, heading_path="A > B")

        trimmed = without_overlap([first, second], preceding_end=None)

        assert (trimmed[1].page_from, trimmed[1].page_to) == (3, 4)
        assert trimmed[1].heading_path == "A > B"

    def test_the_furthest_end_seen_is_what_counts(self) -> None:
        """A short passage nested in a long one must not pull the marker back and
        let the next passage repeat text the long one already showed."""
        long = _passage(0, 0, 30, "L" * 30)
        nested = _passage(1, 5, 10, "n" * 5)
        after = _passage(2, 25, 40, "z" * 15)

        trimmed = without_overlap([long, nested, after], preceding_end=None)

        assert trimmed[2].text == "z" * 10  # 40 - 30

    def test_empty_input_is_empty_output(self) -> None:
        assert without_overlap([], preceding_end=None) == ()


class TestDocumentListQuery:
    def test_defaults_describe_the_working_set_newest_first(self) -> None:
        query = DocumentListQuery()
        assert query.sort is DocumentSort.CREATED_DESC
        assert query.archive is ArchiveFilter.ACTIVE
        assert query.search_text is None

    def test_a_folder_and_unfiled_together_make_no_sense(self) -> None:
        with pytest.raises(BadRequestError, match="not both"):
            DocumentListQuery(folder_id=uuid.uuid4(), unfiled=True)

    def test_too_many_tags_are_refused(self) -> None:
        with pytest.raises(BadRequestError, match="at most"):
            DocumentListQuery(tag_ids=tuple(uuid.uuid4() for _ in range(MAX_FILTER_TAGS + 1)))

    def test_repeating_one_tag_does_not_count_as_many(self) -> None:
        tag = uuid.uuid4()
        DocumentListQuery(tag_ids=(tag,) * (MAX_FILTER_TAGS + 3))

    def test_an_overlong_title_filter_is_refused(self) -> None:
        with pytest.raises(BadRequestError):
            DocumentListQuery(text="x" * (MAX_TEXT_FILTER_LENGTH + 1))

    @pytest.mark.parametrize(
        ("raw", "expected"), [("  budget  ", "budget"), ("   ", None), ("", None)]
    )
    def test_search_text_is_trimmed_and_blank_means_no_filter(
        self, raw: str, expected: str | None
    ) -> None:
        assert DocumentListQuery(text=raw).search_text == expected

    @pytest.mark.parametrize(
        ("sort", "descending"),
        [
            (DocumentSort.CREATED_DESC, True),
            (DocumentSort.CREATED_ASC, False),
            (DocumentSort.UPDATED_DESC, True),
            (DocumentSort.TITLE_ASC, False),
            (DocumentSort.TITLE_DESC, True),
        ],
    )
    def test_direction_is_derived_not_repeated(self, sort: DocumentSort, descending: bool) -> None:
        assert sort.is_descending is descending


class TestDocumentEdit:
    def test_an_edit_that_changes_nothing_is_refused(self) -> None:
        with pytest.raises(BadRequestError, match="Nothing to change"):
            DocumentEdit()

    def test_a_title_alone_is_an_edit(self) -> None:
        assert DocumentEdit(title="New").move_to_folder is False

    def test_unfiling_is_distinct_from_leaving_the_folder_alone(self) -> None:
        unfile = DocumentEdit(move_to_folder=True, folder_id=None)
        assert unfile.move_to_folder is True
        assert unfile.folder_id is None

    def test_a_folder_id_without_the_move_flag_is_a_contradiction(self) -> None:
        with pytest.raises(BadRequestError):
            DocumentEdit(title="x", folder_id=uuid.uuid4())

    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    def test_a_blank_title_is_refused(self, title: str) -> None:
        with pytest.raises(ValidationError, match="blank"):
            DocumentEdit(title=title)

    def test_an_overlong_title_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="exceed"):
            DocumentEdit(title="x" * 513)


class TestSortCursor:
    def test_round_trips(self) -> None:
        row = uuid.uuid4()
        token = SortCursor(sort="title_asc", value="budget", row_id=row).encode(SECRET)
        decoded = SortCursor.decode(token, SECRET, sort="title_asc")
        assert (decoded.sort, decoded.value, decoded.row_id) == ("title_asc", "budget", row)

    def test_a_cursor_cannot_be_replayed_under_another_ordering(self) -> None:
        """Compared against the wrong sort key it would not error -- it would return
        a plausible-looking wrong page."""
        token = SortCursor(sort="title_asc", value="budget", row_id=uuid.uuid4()).encode(SECRET)
        with pytest.raises(BadRequestError, match="not valid"):
            SortCursor.decode(token, SECRET, sort="created_desc")

    def test_a_forged_cursor_is_rejected(self) -> None:
        token = SortCursor(sort="title_asc", value="a", row_id=uuid.uuid4()).encode("another-key")
        with pytest.raises(BadRequestError):
            SortCursor.decode(token, SECRET, sort="title_asc")

    def test_a_tampered_cursor_is_rejected(self) -> None:
        token = SortCursor(sort="title_asc", value="a", row_id=uuid.uuid4()).encode(SECRET)
        flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
        with pytest.raises(BadRequestError):
            SortCursor.decode(flipped, SECRET, sort="title_asc")

    @pytest.mark.parametrize("garbage", ["", "not-a-cursor", "%%%", "a" * 8])
    def test_garbage_is_rejected_without_a_crash(self, garbage: str) -> None:
        with pytest.raises(BadRequestError):
            SortCursor.decode(garbage, SECRET, sort="title_asc")

    def test_the_two_cursor_kinds_do_not_decode_each_other(self) -> None:
        """A creation-time cursor (used elsewhere) is not a sort cursor, and vice versa."""
        time_cursor = Cursor(created_at=datetime.now(UTC), row_id=uuid.uuid4()).encode(SECRET)
        with pytest.raises(BadRequestError):
            SortCursor.decode(time_cursor, SECRET, sort="created_desc")

        sort_cursor = SortCursor(sort="created_desc", value="x", row_id=uuid.uuid4()).encode(SECRET)
        with pytest.raises(BadRequestError):
            Cursor.decode(sort_cursor, SECRET)
