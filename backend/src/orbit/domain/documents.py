"""Listing, editing, and reading documents: the parts that are rules, not storage.

Three self-contained pieces live here, each pure so it can be tested with no
database:

* `DocumentListQuery` -- everything a caller may ask of the document list, with
  the combinations that make no sense refused at construction.
* `DocumentEdit` -- a partial update that can tell "leave the folder alone" from
  "take it out of its folder", which a plain `folder_id: UUID | None` cannot.
* `without_overlap` -- turns stored passages back into continuously readable
  text, using the character offsets the chunker recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from orbit.domain.errors import BadRequestError, ValidationError
from orbit.domain.models.entities import ProcessingStatus

#: How many tags a list may be filtered by at once. Each is an `EXISTS`
#: predicate, so the cost grows with the count; five is more than a person can
#: usefully hold in their head as a filter.
MAX_FILTER_TAGS: Final = 5
MAX_TEXT_FILTER_LENGTH: Final = 100
#: Mirrors the `documents.title` column and the request schema.
MAX_DOCUMENT_TITLE_LENGTH: Final = 512


class DocumentSort(StrEnum):
    """The orderings the list supports.

    Each maps to one index (`ix_documents_*`), so any of them pages in constant
    time. The value is what travels in the URL and inside signed cursors.
    """

    CREATED_DESC = "created_desc"
    CREATED_ASC = "created_asc"
    UPDATED_DESC = "updated_desc"
    TITLE_ASC = "title_asc"
    TITLE_DESC = "title_desc"

    @property
    def is_descending(self) -> bool:
        return self in (
            DocumentSort.CREATED_DESC,
            DocumentSort.UPDATED_DESC,
            DocumentSort.TITLE_DESC,
        )


class ArchiveFilter(StrEnum):
    """Which side of the archive a list shows. There is deliberately no "both":
    archived documents are out of the way, not mixed into the working set."""

    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class DocumentListQuery:
    status: ProcessingStatus | None = None
    #: Documents directly in this folder (not its subfolders).
    folder_id: uuid.UUID | None = None
    #: Documents in no folder. Mutually exclusive with `folder_id`.
    unfiled: bool = False
    #: A document must carry *every* one of these.
    tag_ids: tuple[uuid.UUID, ...] = ()
    #: Case-insensitive substring of the title.
    text: str | None = None
    sort: DocumentSort = DocumentSort.CREATED_DESC
    archive: ArchiveFilter = ArchiveFilter.ACTIVE

    def __post_init__(self) -> None:
        if self.folder_id is not None and self.unfiled:
            msg = "Filter by a folder or by 'unfiled', not both."
            raise BadRequestError(msg)
        if len(set(self.tag_ids)) > MAX_FILTER_TAGS:
            msg = f"A list can be filtered by at most {MAX_FILTER_TAGS} tags."
            raise BadRequestError(msg)
        if self.text is not None and len(self.text.strip()) > MAX_TEXT_FILTER_LENGTH:
            msg = f"The title filter is limited to {MAX_TEXT_FILTER_LENGTH} characters."
            raise BadRequestError(msg)

    @property
    def search_text(self) -> str | None:
        """The trimmed filter, or `None` when there is nothing to filter by."""
        return (self.text or "").strip() or None


@dataclass(frozen=True, slots=True)
class DocumentEdit:
    """A partial change to a document's identity (never its content).

    `title=None` means "leave the title". For the folder there are three
    intents, so a flag carries the distinction a nullable id cannot:
    `move_to_folder=False` leaves it; `True` with `folder_id=None` unfiles it;
    `True` with an id files it there.
    """

    title: str | None = None
    move_to_folder: bool = False
    folder_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.title is None and not self.move_to_folder:
            msg = "Nothing to change: send a title, a folder, or both."
            raise BadRequestError(msg)
        if self.folder_id is not None and not self.move_to_folder:
            msg = "A folder was given but the change does not move the document."
            raise BadRequestError(msg)
        if self.title is not None:
            cleaned = self.title.strip()
            if not cleaned:
                msg = "A document title cannot be blank."
                raise ValidationError(msg)
            if len(cleaned) > MAX_DOCUMENT_TITLE_LENGTH:
                msg = f"A document title cannot exceed {MAX_DOCUMENT_TITLE_LENGTH} characters."
                raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class Passage:
    """One stored passage of a document version, addressed by its ordinal."""

    ordinal: int
    text: str
    #: Offsets into the version's *normalised* text. `text` is exactly
    #: `normalised[char_start:char_end]`, which is what makes trimming by offset
    #: exact rather than a fuzzy comparison of strings.
    char_start: int
    char_end: int
    page_from: int | None = None
    page_to: int | None = None
    heading_path: str | None = None


@dataclass(frozen=True, slots=True)
class PassageSlice:
    """A run of consecutive passages, as stored.

    `preceding_end` is where the passage *before* the slice ended (`None` for the
    first slice), so the overlap at the seam between two pages of results can be
    trimmed the same way as the overlap inside one.
    """

    items: tuple[Passage, ...]
    preceding_end: int | None
    #: Ordinal to pass as `after` for the next slice; `None` on the last.
    next_after: int | None


def without_overlap(
    passages: Sequence[Passage], *, preceding_end: int | None
) -> tuple[Passage, ...]:
    """Remove the text each passage repeats from the one before it.

    The chunker prepends the previous chunk's trailing sentences to the next one
    (ADR-0013) so retrieval does not lose context at a boundary. That is right
    for retrieval and wrong for reading: shown as stored, every boundary would
    repeat a sentence or two. Because each passage records the exact span of the
    normalised text it holds, the repeated prefix is `covered - char_start`
    characters long, and dropping it leaves text that reads continuously.

    Ordinals are preserved, including for a passage trimmed to nothing, so a
    citation that names an ordinal always finds it.
    """
    covered = preceding_end
    trimmed: list[Passage] = []
    for passage in passages:
        current = passage
        if covered is not None and passage.char_start < covered:
            drop = min(covered - passage.char_start, len(passage.text))
            remainder = passage.text[drop:]
            stripped = remainder.lstrip()
            current = replace(
                passage,
                text=stripped,
                char_start=passage.char_start + drop + (len(remainder) - len(stripped)),
            )
        trimmed.append(current)
        covered = passage.char_end if covered is None else max(covered, passage.char_end)
    return tuple(trimmed)
