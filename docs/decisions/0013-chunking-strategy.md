# 0013 — Structure-aware, token-bounded chunking with overlap

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Chunking is the least glamorous stage of a RAG pipeline and the one that most
directly bounds answer quality. Retrieval can only return chunks that exist; if
a fact is split across two chunks so that neither states it completely, no
retrieval strategy and no model recovers it.

The constraints pull against each other. Chunks must be small enough that an
embedding represents one coherent idea — averaging a whole page into 1536
dimensions blurs everything in it — and large enough to be self-contained when
handed to the model with no surrounding context.

## Decision

**Structure-aware, token-bounded chunks with overlap**, built from the
`TextBlock` sequence of ADR-0012 rather than from raw text.

| Parameter | Value | Reason |
|---|---|---|
| Target size | 512 tokens | Comfortably inside `text-embedding-3-small`'s window while keeping one chunk to roughly one idea. |
| Hard maximum | 768 tokens | Absorbs an oversized indivisible block without truncating it. |
| Overlap | 64 tokens | Recovers facts that straddle a boundary. |
| Minimum | 32 tokens | Below this a chunk is noise that pollutes retrieval. Merged forward instead. |

**Block boundaries are respected.** Chunks are assembled by accumulating whole
blocks until the target is reached. A paragraph is not split unless it alone
exceeds the hard maximum, in which case it is split at sentence boundaries.
Splitting mid-sentence is never correct — it produces a fragment that embeds
poorly and reads as broken when shown as a citation snippet.

**Headings are inherited.** Each chunk is prefixed with the heading path it
falls under (`Security > Authentication > Token rotation`). The heading is often
where the discriminating term appears, while the body uses pronouns. This costs
a few tokens and materially improves retrieval on structured documents.

**Every chunk carries full provenance**, propagated from its constituent blocks:
document, ordinal, `page_from`/`page_to`, `char_start`/`char_end`, token count,
and a content hash. This is what ADR-0006 resolves citations against; a chunk
without provenance cannot be cited and is therefore useless.

**Chunking is pure and deterministic.** Same input, same configuration, same
chunks — no I/O, no randomness, no model call. It is unit-tested without
infrastructure, and re-running the pipeline on an unchanged document produces
identical chunks, which is what makes reprocessing idempotent (ADR-0002).

The parameters are configuration, and the configuration version is **recorded on
each chunk row** alongside the embedding model. Without it, a corpus chunked
under two different strategies is silently mixed and there is no way to query
which documents need reprocessing.

## Alternatives considered

**Fixed-size character windows.** Trivial and format-agnostic. Rejected: splits
mid-word and mid-sentence, ignores document structure, and produces citation
snippets that begin and end mid-clause.

**One chunk per paragraph.** Respects structure. Rejected: paragraph length
varies by an order of magnitude, so chunks range from useless one-liners to
oversized blocks, and embedding quality varies with them.

**One chunk per page.** Rejected: a page holds several unrelated ideas, and
averaging them produces an embedding that matches nothing well.

**Semantic chunking** — embed sentences, detect topic shifts, cut at the
boundaries. Genuinely better on some corpora. Rejected for v1: it requires an
embedding call per sentence at ingestion, multiplying cost and latency, and the
published gains over structure-aware chunking are modest. The chunker is a
replaceable stage, and the recorded configuration version makes a future
migration measurable.

**No overlap.** Rejected: it makes boundary-straddling facts unretrievable, and
64 tokens of duplication is cheap.

**Very large chunks (2000+ tokens), relying on the model's long context.**
Rejected: it moves the precision problem from retrieval to generation. Large
chunks retrieve imprecisely, fill the context budget with irrelevant text, cost
more per query, and produce citations too coarse to verify.

## Consequences

- Overlap means roughly 12% more chunks, so slightly more storage, more
  embedding cost, and more index entries. Accepted.
- Overlap also means near-duplicate chunks can both rank highly for the same
  query. Context construction deduplicates by document and adjacency before
  filling the token budget, so the model is not shown the same passage twice.
- Token counting requires the provider's tokenizer to be accurate. The
  deterministic fake provider uses a documented approximation, so chunk counts
  differ slightly between fake and OpenAI runs. Tests assert *properties*
  (bounds respected, no mid-sentence splits, offsets contiguous), never exact
  chunk counts.
- Changing any parameter invalidates the corpus: reprocessing and re-embedding
  are required. The recorded configuration version makes the affected set a
  query rather than a guess.
- Heading inheritance depends on the parser emitting `HEADING` blocks. Plain
  `.txt` has no headings, so those chunks carry no path — correct, not a defect.

## Implementation notes (M4)

- **Token counting is approximate and offline** (`ApproximateTokenCounter`):
  `tiktoken` downloads its encodings on first use, which a worker and a test suite
  that run without network cannot allow. The approximation errs high for English
  and counts one token per non-Latin character; the 768-token ceiling stays an
  order of magnitude inside the embedding model's input limit either way.
- **The heading path is prepended only to the embedding input**, and stored in
  `chunks.heading_path`. `chunks.content` is exactly
  `document.text[char_start:char_end]`, so a citation built from offsets shows
  precisely the retrieved text.
- **Oversized blocks** split at sentence boundaries (a small abbreviation and
  initials guard), then at words, and -- only for a run with no whitespace at
  all -- at a fixed width. Code and tables split at line boundaries. Sentences
  from one block are packed to `target - overlap`, so overlap-prefixed chunks land
  near the target rather than above it.
- **Overlap** is whole trailing sentences, within the overlap budget, never across
  a heading boundary and never from code or tables.
- **Minimum size**: an undersized section is merged forward; an undersized final
  chunk joins the previous one if it fits under the maximum; a document whose
  only chunk is small keeps it.
- **Duplicates**: a chunk whose text matches an earlier chunk of the same
  document (ignoring case and whitespace) is dropped, and ordinals are assigned
  after deduplication. `chunks.content_sha256` records each chunk's hash.
- **`chunker_version`** is `sa1-{target}-{max}-{overlap}-{min}`.

