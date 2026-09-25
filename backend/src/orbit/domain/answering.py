"""Grounded answering: context construction and citation resolution.

Pure -- no I/O, no provider. Two rules live here because they are the whole
guarantee, and a guarantee is easier to trust when it is a small pure
function with its own tests:

**Only retrieved text reaches the model, and only within a budget.** Sources
are chosen from the retrieval results in rank order, deduplicated, and added
until a computed token budget is spent. A document is never sent whole; a
source that does not fit is skipped, not truncated mid-thought.

**Citations are resolved, never parsed (ADR-0006).** Each source gets a short
per-request handle -- `S1`, `S2`, ... -- and the model may only refer to
handles. After generation every handle in the text is looked up in this
request's map. A handle that resolves becomes a citation built from the
chunk's *database record*; one that does not is removed from the text and
counted. No code path turns generated text into a document id, a title, or a
page number.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from orbit.domain.conversations import (
    MAX_CITATION_SNIPPET_CHARS,
    Citation,
    Grounding,
)
from orbit.domain.retrieval import SearchResult

#: What the model writes, alone and first, when the sources do not answer the
#: question. A classification signal only -- it is stripped from the text and
#: never trusted for provenance.
INSUFFICIENT_EVIDENCE_MARKER = "[INSUFFICIENT_EVIDENCE]"

#: Two chunks of one version overlapping by more than this share of the
#: shorter one are the same passage (ADR-0013 chunks overlap by design).
_DUPLICATE_OVERLAP_RATIO = 0.5

#: `[S1]`, and the grouped forms models produce anyway: `[S1, S3]`, `[S2; S4]`.
#: Case-insensitive, because a lowercase `[s1]` left in the text would still
#: read as a citation to the user.
_CITATION_GROUP = re.compile(r"\[\s*(S\d{1,4}(?:\s*[,;]\s*S\d{1,4})*)\s*\]", re.IGNORECASE)
_HANDLE = re.compile(r"S\d{1,4}", re.IGNORECASE)
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?])")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")
_WHITESPACE = re.compile(r"\s+")


def handle_for(position: int) -> str:
    """The handle of the `position`-th source (1-based)."""
    return f"S{position}"


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextSource:
    """One retrieved chunk as it is shown to the model."""

    handle: str
    result: SearchResult
    #: Estimated tokens of the rendered block, header included.
    tokens: int

    @property
    def chunk_id(self) -> uuid.UUID:
        return self.result.chunk.id


@dataclass(frozen=True, slots=True)
class BuiltContext:
    sources: tuple[ContextSource, ...]
    #: The source blocks, rendered, in handle order.
    text: str
    tokens: int
    #: The budget the sources had to fit within.
    budget: int
    #: Candidates skipped as a repeat of a passage already included.
    duplicates_dropped: int
    #: Candidates skipped because they did not fit the remaining budget.
    over_budget_dropped: int

    @property
    def is_empty(self) -> bool:
        return not self.sources

    def handle_map(self) -> Mapping[str, ContextSource]:
        return {source.handle: source for source in self.sources}


def context_budget(
    *,
    context_window: int,
    reserved_output_tokens: int,
    fixed_prompt_tokens: int,
    safety_margin_tokens: int,
    max_context_tokens: int,
) -> int:
    """Tokens available for source text.

    Whatever the window leaves after the instructions, the question, the
    conversation history, the answer's reserve, and a margin for the
    estimate's error -- and never more than the configured ceiling, which is
    what keeps a 128k-token window from being filled with marginal chunks.
    """
    available = context_window - reserved_output_tokens - fixed_prompt_tokens - safety_margin_tokens
    return max(0, min(available, max_context_tokens))


def render_source(handle: str, result: SearchResult) -> str:
    """A source as the model sees it.

    The header carries the handle and what a person would cite -- title,
    pages, section -- so the model can attribute precisely. Attribute values
    are sanitised of quotes and angle brackets, so a document title cannot
    close the tag and smuggle text outside its source block.
    """
    attributes = [f'id="{handle}"', f'title="{_attribute(result.document.title)}"']
    location = result.location
    if location.page_from is not None:
        pages = (
            str(location.page_from)
            if location.page_to in (None, location.page_from)
            else f"{location.page_from}-{location.page_to}"
        )
        attributes.append(f'pages="{pages}"')
    if location.heading_path:
        attributes.append(f'section="{_attribute(location.heading_path)}"')
    body = result.chunk.text.replace("</source>", "</ source>")
    return f"<source {' '.join(attributes)}>\n{body}\n</source>"


def render_question(question: str) -> str:
    """The question, fenced so the model can tell it from the sources."""
    return f"<question>\n{question.replace('</question>', '</ question>')}\n</question>"


#: Parse the two blocks above back out of a prompt. Used by the fake provider,
#: which answers from exactly what a real model would have been shown.
SOURCE_BLOCK = re.compile(r'<source id="(S\d+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)
QUESTION_BLOCK = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)


def build_context(
    results: Sequence[SearchResult],
    *,
    budget: int,
    max_sources: int,
    count_tokens: Callable[[str], int],
) -> BuiltContext:
    """Choose, deduplicate, and render sources, best first, within `budget`.

    A source that does not fit is skipped and the next one tried: a shorter,
    slightly lower-ranked passage is better evidence than none. Handles are
    assigned after selection, so they are dense (`S1..Sn`) whatever was
    skipped.
    """
    chosen: list[tuple[SearchResult, str, int]] = []
    seen_content: set[str] = set()
    used = duplicates = over_budget = 0

    for result in results:
        if len(chosen) >= max_sources:
            break
        fingerprint = _content_fingerprint(result.chunk.text)
        if fingerprint in seen_content or any(
            _same_passage(result, earlier) for earlier, _, _ in chosen
        ):
            duplicates += 1
            continue
        # Measured with the widest handle it could receive, so assigning the
        # real handle afterwards can only shrink it.
        block = render_source(handle_for(max_sources), result)
        tokens = count_tokens(block)
        if used + tokens > budget:
            over_budget += 1
            continue
        chosen.append((result, block, tokens))
        seen_content.add(fingerprint)
        used += tokens

    sources = tuple(
        ContextSource(handle=handle_for(position), result=result, tokens=tokens)
        for position, (result, _, tokens) in enumerate(chosen, start=1)
    )
    return BuiltContext(
        sources=sources,
        text="\n\n".join(render_source(s.handle, s.result) for s in sources),
        tokens=used,
        budget=budget,
        duplicates_dropped=duplicates,
        over_budget_dropped=over_budget,
    )


def _same_passage(candidate: SearchResult, earlier: SearchResult) -> bool:
    if candidate.version.id != earlier.version.id:
        return False
    a, b = candidate.location, earlier.location
    overlap = min(a.char_end, b.char_end) - max(a.char_start, b.char_start)
    if overlap <= 0:
        return False
    shorter = min(a.char_end - a.char_start, b.char_end - b.char_start)
    return shorter > 0 and overlap / shorter > _DUPLICATE_OVERLAP_RATIO


def _content_fingerprint(text: str) -> str:
    """Identical passages in different documents (a file uploaded twice under
    two names) are shown once."""
    return hashlib.sha256(_WHITESPACE.sub(" ", text).strip().casefold().encode()).hexdigest()


def _attribute(value: str) -> str:
    return value.replace('"', "'").replace("<", "(").replace(">", ")").replace("\n", " ")


# ---------------------------------------------------------------------------
# Citation resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    #: The answer with only resolvable citation markers left in it, and the
    #: insufficient-evidence marker removed.
    text: str
    #: In order of first appearance in the text.
    citations: tuple[Citation, ...]
    #: Distinct handles the model wrote that were not in this request's map.
    discarded_handles: tuple[str, ...]
    declared_insufficient: bool


def resolve_answer(raw_text: str, sources: Mapping[str, ContextSource]) -> ResolvedAnswer:
    """Bind the model's handles to retrieved chunks; drop the rest.

    Every citation is built by `citation_from` from the `SearchResult` that
    retrieval materialised from the database. The generated text supplies
    only the handle, and a handle outside `sources` produces nothing but a
    count.
    """
    declared = INSUFFICIENT_EVIDENCE_MARKER.casefold() in raw_text.casefold()
    text = re.sub(re.escape(INSUFFICIENT_EVIDENCE_MARKER), "", raw_text, flags=re.IGNORECASE)

    cited: list[str] = []
    discarded: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        valid: list[str] = []
        for raw_handle in _HANDLE.findall(match.group(1)):
            handle = raw_handle.upper()
            if handle in sources:
                if handle not in valid:
                    valid.append(handle)
                if handle not in cited:
                    cited.append(handle)
            elif handle not in discarded:
                discarded.append(handle)
        return "".join(f"[{handle}]" for handle in valid)

    text = _CITATION_GROUP.sub(rewrite, text)
    text = _tidy(text)
    return ResolvedAnswer(
        text=text,
        citations=tuple(
            citation_from(sources[handle], ordinal) for ordinal, handle in enumerate(cited)
        ),
        discarded_handles=tuple(discarded),
        declared_insufficient=declared,
    )


def citation_from(source: ContextSource, ordinal: int) -> Citation:
    result = source.result
    return Citation(
        handle=source.handle,
        ordinal=ordinal,
        document_id=result.document.id,
        document_title=result.document.title,
        document_version_id=result.version.id,
        version_number=result.version.version_number,
        chunk_id=result.chunk.id,
        chunk_ordinal=result.chunk.ordinal,
        page_from=result.location.page_from,
        page_to=result.location.page_to,
        heading_path=result.location.heading_path,
        char_start=result.location.char_start,
        char_end=result.location.char_end,
        snippet=snippet(result.chunk.text),
    )


def snippet(text: str, limit: int = MAX_CITATION_SNIPPET_CHARS) -> str:
    """The chunk text, cut at a word boundary to fit the snapshot column."""
    collapsed = _WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[: limit - 1]
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > limit // 2 else cut).rstrip() + "…"


def _tidy(text: str) -> str:
    """Repair the spacing that removing a marker leaves behind, without
    touching line structure (lists and paragraphs survive)."""
    lines = [
        _RUN_OF_SPACES.sub(" ", _SPACE_BEFORE_PUNCTUATION.sub(r"\1", line)).rstrip()
        for line in text.splitlines()
    ]
    return "\n".join(lines).strip()


def classify_grounding(
    *, sources_available: int, declared_insufficient: bool, citations: int
) -> Grounding:
    if sources_available == 0:
        return Grounding.NO_EVIDENCE
    if declared_insufficient:
        return Grounding.INSUFFICIENT_EVIDENCE
    if citations == 0:
        return Grounding.UNCITED
    return Grounding.GROUNDED


def cited_chunk_ids(citations: Iterable[Citation]) -> set[uuid.UUID]:
    return {c.chunk_id for c in citations if c.chunk_id is not None}
