# 0015 — Structured logs and Prometheus metrics now, distributed tracing deferred

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Sections 9 and 10 of the brief require that a production failure be
investigable: structured logs with correlation IDs, latency and error metrics
across every dependency, and an error-tracking abstraction.

The defining difficulty in ORBIT is that **the interesting failures cross a
process boundary asynchronously.** A user uploads a document and it never
becomes `READY`. The HTTP request that started it succeeded, returned 202, and
finished seconds before the failure happened — in a different process, possibly
on a different host, possibly after two retries.

If the API request and the worker's attempts cannot be correlated, that
investigation begins by grepping timestamps.

## Decision

### Correlation identifiers, propagated across the queue

Every request is assigned a `request_id` (ULID: sortable, and unlike a UUIDv4
its ordering is useful in a log viewer) at the outermost middleware, held in a
`contextvar`, and attached to every log record automatically.

Crucially, the `request_id` is **carried into the Celery task payload** and
restored into the worker's logging context, and every retry of that task logs
under the same id. One document's entire lifecycle — upload request, enqueue,
three worker attempts, final failure — is retrievable with a single query.

Documents also carry a stable `document_id` through every stage, which is the
identifier a support conversation will actually start from.

### Structured logging with `structlog`

JSON in every deployed environment, human-readable in local development. A fixed
set of bound fields — `timestamp`, `level`, `service`, `environment`, `version`,
`request_id`, and, where applicable, `user_id`, `workspace_id`, `document_id`,
`job_id`, `operation` — so queries are field lookups rather than regexes.

Redaction is **structural, not diligent**. A processor drops known-sensitive
keys (`password`, `token`, `authorization`, `secret`, `api_key`,
`refresh_token`) anywhere in an event, and document text is never a log field —
only its length and hash. Relying on every developer to remember is not a
control; a processor is.

### Prometheus metrics

`prometheus-client`, exposed on a **separate port** bound to the internal
network, not on the public API. `/metrics` on the public app is an information
leak and an unauthenticated scrape target.

The instrumented surface, matching section 10:

| Area | Metric shape |
|---|---|
| HTTP | request duration histogram, in-flight gauge, responses by status class |
| Database | query duration histogram, pool in-use / available gauges |
| Queue | task duration, outcome counter (success/retry/permanent failure), **queue depth**, task age at start |
| Documents | end-to-end processing duration by stage and format, failures by reason |
| AI | provider latency histogram, token counter, error counter, **citation-discard counter** (ADR-0006) |
| Storage | operation duration, failure counter |
| Cache | hit/miss counters |

Label cardinality is a hard rule: **no `user_id`, `workspace_id`, `document_id`,
or raw path in a metric label.** Route *templates*, not URLs. Unbounded label
cardinality is the standard way to destroy a Prometheus server, and the
identifiers belong in logs, where they are cheap.

### Error tracking behind a port

```python
class ErrorReporter(Protocol):
    def capture(self, exc: BaseException, *, context: Mapping[str, object]) -> None: ...
```

No-op by default; a Sentry adapter is added when there is somewhere to send it.
The port exists now so the call sites are correct from the start.

### Health endpoints are two different questions

- **`/healthz` — liveness.** Checks nothing external. Returns 200 if the process
  is running. A liveness probe that checks the database restarts every API pod
  during a database blip, turning a recoverable dependency failure into a
  self-inflicted outage.
- **`/readyz` — readiness.** Checks database, Redis, and object storage with
  short timeouts, and reports per-dependency status. Failing removes the
  instance from rotation without killing it.

### Distributed tracing is deferred, deliberately

OpenTelemetry tracing is the correct long-term answer, and it is not adopted
now. It requires a collector and a trace backend to run, secure, and pay for,
and its value over correlated structured logs is modest while there are two
services and a queue.

What *is* done now is making adoption additive rather than a rewrite:
`request_id` propagation follows the shape of W3C `traceparent`, context is
already threaded through the queue boundary, and instrumentation lives in
middleware and decorators rather than scattered through business logic. Adopting
OTel later means changing the emitter, not the call sites.

## Alternatives considered

**OpenTelemetry for logs, metrics, and traces now.** Rejected as above:
operational cost ahead of demonstrated need. Revisit when a third service exists
or when a latency question cannot be answered from logs.

**Vendor SDK (Datadog, New Relic) as the primary instrumentation.** Rejected:
couples application code to a vendor for the observability layer specifically,
which is the layer most needed during a migration away from that vendor.

**Logs only, no metrics.** Rejected: alerting on log volume is imprecise and
expensive, and there is no cheap way to ask "what is p99 right now".

**`/metrics` on the public API port, protected by a token.** Rejected: one
misconfiguration from public exposure, when a separate internal port makes it
structurally unreachable.

## Consequences

- Every Celery task signature carries a correlation context. This is enforced by
  a shared task base class rather than left to each task author.
- Log volume is a real cost. Sampling is applied to high-frequency `INFO` events
  in production; `WARNING` and `ERROR` are never sampled.
- The metrics port must not be exposed by the ingress. This belongs in the
  deployment checklist and in `docs/operations/observability.md`.
- Local development runs without Prometheus. Metrics are still collected in
  process, so the endpoint is exercised, but nothing scrapes it.
- The citation-discard counter is both a quality signal and an alert: a sustained
  rise means the model, prompt, or handle format has regressed.

## Amendment (2026-09-24): implementation notes

- **Metrics** are defined in `orbit.core.metrics`; each states the question it
  answers, and tests enforce bounded labels. Catalogue and alerts:
  [observability.md](../operations/observability.md). The table above is the
  plan; the catalogue is what shipped.
- **`operation`** joined the correlation fields; `user_id`, `workspace_id`, and
  `document_id` are now bound on the API side. The request middleware is pure
  ASGI so streamed responses are timed to their last byte and bindings made by
  handlers reach the access-log line.
- **Readiness distinguishes critical from non-critical dependencies.** The
  original text said any failed dependency removes the instance from rotation.
  That contradicts ADR-0024 (Redis is disposable) and the failure-modes
  contract, so only PostgreSQL and the vector schema are critical; the others
  report `degraded` with `200`. The language-model circuit breaker is reported
  passively -- no paid probe.
- **AI provider correlation:** ORBIT's `request_id` is sent as
  `X-Client-Request-Id`; the provider's `x-request-id` is captured on failures.
- **Metrics port** is loopback by default, and multi-process aggregation needs
  `PROMETHEUS_MULTIPROC_DIR`.
- **Not done:** the `ErrorReporter` port and distributed tracing.

