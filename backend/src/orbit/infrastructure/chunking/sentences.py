"""Sentence boundary detection, for splitting oversized blocks and for overlap.

Rule-based and deliberately modest. A boundary is terminal punctuation,
optional closing quotes or brackets, whitespace, then something that can start
a sentence. The guard against the common false positive -- an abbreviation or
an initial ("e.g. this", "J. Smith") -- is a short list, not a model: missing a
boundary only makes a unit longer, while a false boundary cuts a sentence, which
ADR-0013 calls never correct.
"""

from __future__ import annotations

import re

_BOUNDARY = re.compile(
    # terminal punctuation, closing quotes/brackets, whitespace ...
    r"[.!?\u2026]+[\"'\u201d\u2019)\]]*(\s+)"
    # ... then something that can start a sentence.
    r"(?=[\"'\u201c\u2018(\[]?[A-Z0-9\u00c0-\u00d6\u00d8-\u00de])"
)
_PREVIOUS_WORD = re.compile(r"(\S+)$")
_ABBREVIATIONS = frozenset(
    {
        "e.g",
        "i.e",
        "cf",
        "vs",
        "etc",
        "al",
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "no",
        "fig",
        "figs",
        "eq",
        "inc",
        "ltd",
        "co",
        "corp",
        "u.s",
        "u.k",
        "approx",
        "dept",
        "vol",
        "pp",
    }
)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Contiguous `(start, end)` spans covering `text`, one per sentence.

    Each span ends at its terminal punctuation (trailing whitespace belongs to
    the gap), so `text[start:end]` is the sentence exactly.
    """
    spans: list[tuple[int, int]] = []
    start = _skip_space(text, 0)
    for match in _BOUNDARY.finditer(text):
        end = match.start(1)
        if end <= start or _is_abbreviation(text[start:end]):
            continue
        spans.append((start, end))
        start = match.end(1)
    tail_end = len(text.rstrip())
    if tail_end > start:
        spans.append((start, tail_end))
    return spans


def _is_abbreviation(sentence: str) -> bool:
    match = _PREVIOUS_WORD.search(sentence)
    if match is None:
        return False
    word = (
        match.group(1).rstrip(".!?\u2026\"'\u201d\u2019)]").lstrip("(\"'\u201c\u2018[").casefold()
    )
    # A single letter is an initial ("J. Smith") far more often than a sentence.
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index
