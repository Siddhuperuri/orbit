# 0012 — Parsers produce normalized text with provenance

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

ORBIT supports PDF, Markdown, and plain text at launch, and the brief requires
that images, web pages, and further formats be addable **without rewriting the
system**.

The naive interface — `parse(bytes) -> str` — satisfies today's formats and
makes tomorrow's impossible. It discards the information citations depend on. A
citation must name a page and a location; once a PDF has been flattened to a
string, the page a passage came from is gone, and no downstream stage can
recover it.

The extension point is therefore not "which parsers exist" but **what a parser
is obliged to produce**.

## Decision

Every parser produces the same structure, regardless of input format:

```python
@dataclass(frozen=True)
class TextBlock:
    text: str
    ordinal: int                 # position in reading order
    page: int | None             # 1-based; None where the format has no pages
    char_start: int              # offset into the normalized document text
    char_end: int
    kind: BlockKind              # PARAGRAPH | HEADING | LIST_ITEM | TABLE | CAPTION | CODE

@dataclass(frozen=True)
class ParsedDocument:
    blocks: tuple[TextBlock, ...]
    page_count: int | None
    detected_language: str | None
    metadata: Mapping[str, str]  # title, author — advisory only, never trusted
```

The parser's entire responsibility is **normalized text plus provenance**. It
does not chunk, does not embed, does not summarise, and does not decide what is
relevant.

That contract is what makes new formats cheap. Chunking, embedding, retrieval,
and citation rendering operate on `ParsedDocument` and have no knowledge of file
formats. Adding OCR for images means writing a parser that emits blocks with
`page` set and coordinates in `metadata`; nothing downstream changes. Adding web
pages means a parser that strips chrome and emits blocks with `page = None`.

### Resolution by sniffed content type

```python
class DocumentParser(Protocol):
    content_types: frozenset[str]
    def parse(self, source: BinaryIO, *, limits: ParseLimits) -> ParsedDocument: ...
```

A registry resolves a parser from the content type **determined by inspecting
bytes** (ADR-0011), never from the file extension or the client's declared type.
An unresolvable type is a typed `UnsupportedContentType` error, not a fallback
to "treat it as text" — silently parsing an unknown binary as UTF-8 produces
garbage chunks and garbage embeddings that are then indistinguishable from real
content.

### Parsers are hostile-input handlers

Parsers run only in the worker, never in an API process, and only under the
process isolation and hard time limits of ADR-0002. Each parser additionally
receives explicit `ParseLimits` — maximum pages, maximum extracted characters,
maximum blocks — because a hard timeout alone permits a document that produces
50 million characters just under the deadline, which then breaks the embedding
stage instead.

Extracted metadata (title, author) is **advisory**. It comes from the document
and is therefore attacker-controlled; it is never rendered as HTML and never
used for authorization or path construction.

### Normalization is shared, not per-parser

Unicode NFC normalization, whitespace collapsing, control-character stripping,
de-hyphenation across line breaks, and ligature expansion happen **once**, in a
shared stage after parsing. Duplicating them per parser guarantees the PDF and
Markdown paths drift, which shows up as chunks that embed differently for
identical text.

## Alternatives considered

**`parse(bytes) -> str`.** Rejected: destroys provenance, so page-level
citations become impossible and the format-extension requirement is unmet.

**A document-conversion service (Apache Tika, `unstructured`, LlamaParse).**
Broad format support immediately. Rejected for now: Tika means running a JVM
service to own and secure; `unstructured` pulls a very large dependency tree
including ML models; hosted parsers send customer documents to a third party,
which is a data-governance decision, not a technical one. The `DocumentParser`
port means any of them can later be adopted as *one implementation* behind the
existing interface — which is precisely the migration path.

**Per-format ad-hoc handling inside the pipeline.** Rejected: format `if`
statements would spread across chunking, embedding, and citation rendering.

**Preserving full layout geometry** (bounding boxes for every span). Rejected as
a default: significant complexity and storage for a feature — highlight-on-page
— that is not yet built. `TextBlock.metadata` leaves room to add coordinates per
parser when a viewer needs them.

## Consequences

- PDF text extraction is imperfect. Multi-column layouts, tables, and scanned
  pages are the known weak cases. **A PDF with no extractable text is detected
  and reported as a distinct, user-visible failure reason** — "this looks like a
  scanned document; OCR is not yet supported" — rather than silently producing a
  document with zero chunks that answers no questions.
- `char_start`/`char_end` are offsets into the normalized text, so normalization
  must run before offsets are assigned, and its output is what gets stored.
  Chunk offsets and stored text cannot disagree.
- Every parser needs a corpus of malformed fixtures: truncated files, encrypted
  PDFs, zero-byte files, wrong magic bytes, enormous page counts, deeply nested
  structures. These are unit tests, run without infrastructure.
- Detected language is captured per document so the PostgreSQL full-text
  configuration in ADR-0005 can eventually be selected per document instead of
  being fixed to `english`.

## Implementation notes (M4)

Recorded here rather than silently diverging from the sketch above. None changes
the decision; each sharpens it.

- **Two shapes, not one.** Parsers return an `ExtractedDocument` of
  `ExtractedBlock`s (raw text, kind, `SourceLocation`, heading level). The shared
  `DocumentNormalizer` turns that into the `ParsedDocument` of `TextBlock`s this
  ADR describes. Parsers *cannot* assign `char_start`/`char_end`, because they
  never see the normalized text -- which is exactly the ordering constraint in
  Consequences, now enforced by the types rather than by convention.
- **Pages are a range.** `TextBlock` carries `page_start`/`page_end`: a paragraph
  that a page break split is rejoined by normalization into one block spanning
  both pages. `SourceLocation` also carries `line_start`/`line_end` for Markdown
  and plain text.
- **Heading paths are assigned by normalization**, not by the chunker, so every
  block carries the section it belongs to.
- **Running headers and footers** ("Page 3 of 12", a repeated title) are removed
  from paged documents during normalization, learned only from pages long enough
  to distinguish an edge from a body.
- **Parser port takes `IO[bytes]`**: the worker streams the object from storage
  into a spooled temporary file, verifying size and SHA-256 against the upload
  record before a parser sees a byte.
- **Libraries:** `pypdf` (pure Python, BSD) and `markdown-it-py` (token stream with
  source line maps). Plain text and Markdown share a decoder that honours BOMs,
  detects BOM-less UTF-16, falls back to Windows-1252, and refuses text that
  decodes into control characters.
- **Scanned PDFs** fail as `DOCUMENT_NO_EXTRACTABLE_TEXT` both when there is no
  text at all and when a document of three or more pages averages under twenty
  visible characters per page (a scan with a stray stamp or page number).

