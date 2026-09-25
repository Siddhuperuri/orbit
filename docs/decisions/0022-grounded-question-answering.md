# 0022 — Grounded question answering: two-phase pipeline, stage-typed failures, one answer row per question

- **Status:** Accepted — implements 0006 and the `LLMProvider` port of 0007
- **Date:** 2026-09-18

## Context

M6 turns retrieval (ADR-0021) into answers. The brief sets the constraints:
answers must be grounded in retrieved evidence; citations must come from
retrieved chunks and never be invented (ADR-0006); no vendor SDK may leak into
routes or use cases (ADR-0007); the system must run with no live model in
tests; streaming is wanted but must not cost correctness; and timeouts,
outages, rate limits, malformed responses, empty retrieval, cancellation,
partial generation, and persistence failure must all be handled — with
retrieval failure clearly distinguishable from generation failure.

Three design pressures pull against each other:

1. **Streaming vs. correctness.** Tokens reach the user before the answer is
   finished, but citation resolution needs the finished answer.
2. **Streaming vs. error reporting.** Once a `200 text/event-stream` has
   started, an HTTP error status is no longer available.
3. **Long-running work vs. ordering.** Generation takes seconds; a
   conversation's messages must stay strictly ordered, and a request can die at
   any point.

## Decision

### Pipeline

```
question ─▶ normalise ─▶ record turn: question + PENDING answer (one txn, row lock)
         ─▶ hybrid retrieval (ADR-0021), top K = 12
         ─▶ Reranker port (pass-through default), top 8; may reorder/cut, never add
         ─▶ context: dedupe (content hash; >50% char overlap within a version),
            rank order, skip-if-over-budget, max 8 sources, handles S1..Sn
         ─▶ LLMProvider (stream or complete) under a wall-clock deadline
         ─▶ resolve handles against the request map; drop and count the rest
         ─▶ PENDING → COMPLETE | PARTIAL | FAILED, exactly once, with citations
```

The context budget is `min(ORBIT_ANSWER_MAX_CONTEXT_TOKENS, window − answer
reserve − (system + history + question) − 256)`. Whole documents are never
sent: each source is one chunk (≤ 768 tokens, ADR-0013), and a source that
does not fit is skipped rather than truncated.

**With nothing to ground on, the model is not called.** Empty retrieval
returns a fixed `no_evidence` answer; a context budget too small for any
source returns `insufficient_evidence`. Asking a model anyway invites exactly
the unsupported answer the system exists to prevent.

### Two phases

`AnswerQuestion.prepare` records the question and retrieves; every failure
here is an ordinary exception. `AnswerQuestion.events` generates and yields
`started`, `retrieval`, `delta`…, then exactly one `done` or `error`. The
streaming endpoint calls `prepare` **before** opening the stream, so 404, 409,
422, 429 and `RETRIEVAL_FAILED` are plain HTTP errors even when streaming;
only generation-stage failures arrive as an `error` event. The non-streaming
endpoint consumes the same generator, so there is one implementation.

**Deltas are provisional; `done` is authoritative.** A delta may contain a
handle that will not resolve. The `done` message carries the resolved text and
the citations, and is what is persisted. The only thing filtered from deltas is
the leading `[INSUFFICIENT_EVIDENCE]` marker.

The provider stream runs in its own task feeding a queue; the consumer waits
on the queue with the deadline. Timeouts and client cancellation therefore
never fire inside a `yield`, and the provider's connection is closed by
cancelling that task.

### Grounding

The versioned system prompt (`grounded-qa/2026-09-18.1`, stored per message)
instructs the model to use the supplied context, avoid unsupported claims,
declare insufficient evidence with a marker, cite only ids present in the
request, mark inference and disagreement, answer the question actually asked,
and treat source text as data, not instructions. Sources and question are
fenced (`<source id="S1" …>`, `<question>`); attribute values and bodies are
sanitised so a document cannot close its own tag.

History (≤ 6 messages, ≤ 1,500 tokens) is included **with citation markers
stripped**: handles are per request, so a stale `[S2]` copied from history
could resolve to the wrong chunk.

Each answer is classified `grounded` | `uncited` | `insufficient_evidence` |
`no_evidence` and returned to the client, which must render anything but
`grounded` as weaker.

### Failure taxonomy

| Stage | Code | HTTP | Recorded as |
|---|---|---|---|
| Conversation (record question / save answer) | `CONVERSATION_UNAVAILABLE` | 503 | question not recorded / answer left PENDING → abandoned |
| Retrieval dependency | `RETRIEVAL_FAILED` | 503 | FAILED, `failure_stage=retrieval`; model not called |
| Embedding outage in hybrid mode | — (not a failure) | 200 | answer with `retrieval_degraded=semantic_unavailable` |
| Provider outage / 5xx / circuit open | `GENERATION_FAILED` | 503 | FAILED, or PARTIAL if text was produced |
| Provider rate limit | `GENERATION_RATE_LIMITED` | 503 + `Retry-After` | as above |
| Deadline or provider timeout | `GENERATION_TIMEOUT` | 503 | as above |
| Malformed / empty response | `GENERATION_FAILED` | 503 | `stop_reason=malformed_response` |
| Output budget exhausted | — | 201 | PARTIAL, `stop_reason=max_tokens` |
| Client disconnect | — | — | PARTIAL or FAILED `ANSWER_CANCELLED` |
| Defect (4xx from provider, bug) | `INTERNAL_ERROR` | 500 | FAILED, cause logged |

Retrieval and generation errors are sibling subclasses of
`DependencyUnavailableError` — neither is a subtype of the other — and the
provider's own error is chained and logged, never returned.

### Persistence

An assistant message is inserted `pending` when its question is accepted and
makes **one** conditional transition to a terminal status. The conversation row
is locked while ordinals are assigned, and `uq_messages_one_pending_per_conversation`
makes "one answer in flight" a database invariant: a second question during
generation is `409 ANSWER_IN_PROGRESS`. A pending row older than 10 minutes
(well past the 60 s generation deadline) belonged to a dead request and is
closed as `failed / abandoned` by the next turn.

Terminal writes run in a detached task and are awaited through
`asyncio.shield`, so a client disconnecting mid-answer cannot cancel the record
of what it was shown. Shutdown awaits outstanding writes.

Citations are snapshots (document, version id and number, chunk id and
ordinal, pages, heading path, offsets, snippet) of the retrieved chunk record;
the chunk link is `ON DELETE SET NULL`, so a citation still renders after
reprocessing. The composite `(workspace_id, document_id)` foreign key makes a
cross-tenant citation unrepresentable.

### Resilience

`ResilientLLMProvider` wraps every real adapter: at most 2 retries with
jittered backoff, a `Retry-After` honoured up to 4 s (longer goes back to the
user as "busy"), and a circuit breaker (5 consecutive failures, 30 s
cool-down, one probe). **A stream is retried only before its first event.**
Rate limiting does not trip the breaker. Unlike the worker's embedding path
(ADR-0020), the API is long-lived, so a breaker's state is meaningful.

### Measurement

Per answer: retrieval, generation, first-token and total latency; retrieved
count; context tokens; provider-reported prompt/completion tokens; discarded
handle count; model id; prompt version; reranker. Persisted on the message and
logged as `answer.finished`. `answer.citations_discarded` is logged separately
as the alertable ADR-0006 signal.

## Alternatives considered

**Resolve citations incrementally during streaming.** Rejected: a handle can
be split across deltas, and the final text still has to be resolved. Doing it
twice creates two sources of truth; doing it once at the end and declaring the
`done` event authoritative is simpler and cannot disagree with storage.

**Open the stream first and report every failure as an event.** Rejected:
clients would have to parse a `200` body to learn that the conversation does
not exist. Retrieval is fast (tens of ms) and runs before the stream opens.

**Insert the assistant message only when the answer is complete.** Rejected:
a concurrent question would take its ordinal, and a crash would leave a
question with no answer and no trace of why. The `pending` row reserves the
slot and makes abandonment detectable.

**Call the model even with no retrieved context, and let the prompt handle it.**
Rejected: costs a call to produce an answer the system has already decided,
and gives a model the chance to answer from its own knowledge.

**Structured output (JSON) for citations.** Deferred, not rejected: it
constrains shape, not referent (ADR-0006), and breaks natural streaming. Handle
resolution remains mandatory either way.

**Query rewriting / HyDE for follow-up questions.** Deferred: retrieval uses
the current question alone, so an elliptical follow-up ("and for adoption?")
retrieves poorly. Measurable later against the retrieval benchmark.

**A cancel endpoint.** Deferred: cancellation is the client closing the
stream. An explicit endpoint needs cross-process signalling (Redis) to reach
the process holding the stream.

## Consequences

- The entire path — including every failure in the taxonomy — is exercised
  with no provider: the fake LLM answers extractively from its prompt, and a
  scripted double produces outages, rate limits, hangs, mid-stream drops, and
  fabricated handles on demand.
- A streamed answer can visibly change at `done` (a fabricated handle
  disappears). Clients must replace the provisional text with the final one.
- Retrieval ignores conversation history; follow-up quality depends on the
  user restating the subject.
- Token budgets use the approximate counter (ADR-0013), which errs high; the
  256-token margin absorbs its error. Exact counts come from the provider.
- The PENDING recovery relies on the next turn; a conversation nobody returns
  to keeps a PENDING row. It is harmless (never shown as an answer) and
  sweepable if it ever matters.
