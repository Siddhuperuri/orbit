# 0006 — Citations are resolved against retrieved chunks, never generated

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Section 14 of the engineering brief states that the LLM must not be allowed to
invent citations. The word *allowed* is the important one. It describes a
property the system enforces, not a behaviour the model is asked to exhibit.

The distinction is not pedantic. A citation is a **trust primitive**: it is the
affordance that lets a user verify an answer instead of believing it. A
fabricated citation is worse than no citation, because it converts an unverified
claim into an apparently verified one. Users stop checking sources precisely
when sources look reliable.

Prompting ("only cite the provided sources", "never invent references") reduces
the rate. It cannot make the rate zero, and a rate above zero is a system that
lies convincingly at some frequency.

## Decision

Citations are **resolved, not parsed**. The model never produces a citation that
reaches the user; it produces a *reference to something the application already
holds*.

1. Retrieval yields an ordered candidate set. The application assigns each chunk
   an opaque, per-request handle (`S1`, `S2`, …) and holds the mapping
   `handle → chunk_id` **server-side**. Handles are per-request, so they cannot
   be replayed or guessed across conversations.
2. The prompt presents only those handles and instructs the model to cite them.
3. The response is parsed for handles. Every extracted handle is looked up in
   the request-scoped map.
4. **Any handle that does not resolve is discarded**, and the discard is
   counted, logged with the request ID, and surfaced as a metric.
5. Citation metadata returned to the client — document, title, page, chunk
   offsets, snippet — is read from the **database record of the retrieved
   chunk**, never from model output.

The model therefore cannot emit a citation that points at a document that was
not retrieved, at a page that does not exist, or at text it invented. There is
no code path from generated tokens to citation metadata.

## Alternatives considered

**Instruct the model to output document titles or URLs.** Rejected: titles are
free text, so the model can produce a plausible one that was never retrieved,
and nothing downstream can tell the difference.

**Post-hoc verification** — generate freely, then check each citation against
the corpus. Rejected: an extra retrieval round trip per citation, and it detects
fabrication rather than preventing it. The prevention is cheaper and stronger.

**Structured output / function calling for citations.** Genuinely useful and
compatible with this design — it constrains *shape*. Rejected as the primary
mechanism because it does not constrain *referent*: a schema-valid response can
still name a chunk ID that was never retrieved. Resolution remains mandatory.
Structured output may be layered on later as a parsing convenience.

**Trusting the model because current models are good at this.** Rejected: it
makes a correctness property depend on a vendor's model version, silently
regressing on any model upgrade.

## Consequences

- The answer text and the citation list are produced by **different mechanisms**,
  and only the citation list is trusted for provenance.
- Discarded handles are a **first-class quality signal**. A rising discard rate
  means the prompt, the model, or the handle format has regressed, and it is
  alertable.
- A model that cites nothing produces an answer with no citations. That is the
  correct outcome, and the UI must present an uncited answer as visibly weaker
  rather than hiding the distinction.
- The citation contract is testable **without any AI provider**: the fake LLM in
  the test suite deliberately emits fabricated handles, and the test asserts
  that zero fabricated citations reach the response. This test is a permanent
  guard, not a one-off.
- Handles must be cheap for the model to reproduce exactly. Short ASCII tokens
  are used deliberately; UUIDs would raise the transcription error rate and thus
  the discard rate.
