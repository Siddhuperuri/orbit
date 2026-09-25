"""Prometheus metrics: every series ORBIT exports, defined in one place.

The rule for adding a metric is that it must answer a question an operator asks
during an incident or a capacity review, and that question is written beside it
below. A series nobody would ever query is cost (storage, scrape time,
cardinality) without value, so ``docs/operations/observability.md`` lists each
one with the alert or runbook step that uses it.

**Labels are bounded, always.** Never a user, workspace, document, job, or raw
URL -- those are unbounded, and unbounded label values are the standard way to
take down a Prometheus server. Identifiers belong in logs, where they are cheap
(ADR-0015). Where a label value comes from outside the process (a content type,
a route) it passes through :func:`bounded` or is a route *template*.

**Process model.** The API is one process per uvicorn worker and the Celery
worker is prefork, so a scrape of one process sees a fraction of the truth. Set
``PROMETHEUS_MULTIPROC_DIR`` and the served registry aggregates every process's
samples (see :func:`serving_registry`). Gauges therefore declare a
``multiprocess_mode``: a gauge that is the *same fact* reported by every process
(queue depth, read from PostgreSQL) uses ``livemax`` so N processes do not
report N times the depth.

This module lives in ``core`` so both the application and infrastructure layers
may record metrics without depending on each other.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    multiprocess,
)

# -- Buckets -------------------------------------------------------------------
# Chosen around what each operation *should* cost, so the interesting boundary
# (the SLO, the timeout) falls inside the range rather than in the last bucket.

_HTTP_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
_DB_BUCKETS: Final = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15)
_DEPENDENCY_BUCKETS: Final = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2)
_AI_BUCKETS: Final = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)
_STAGE_BUCKETS: Final = (0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 900)
_WAIT_BUCKETS: Final = (0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 900, 1800, 3600, 14_400)
_STORAGE_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

_PREFIX: Final = "orbit_"

# -- Build ---------------------------------------------------------------------

BUILD_INFO = Gauge(
    f"{_PREFIX}build_info",
    "Constant 1, labelled with the running version. Answers: which build produced "
    "this behaviour -- did the regression start with a deploy?",
    ["service", "version", "environment"],
    multiprocess_mode="livemax",
)

# -- HTTP ----------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    f"{_PREFIX}http_requests_total",
    "Completed HTTP requests. Answers: traffic and 5xx rate per endpoint.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    f"{_PREFIX}http_request_duration_seconds",
    "Wall time from request received to last response byte written, so a streamed "
    "answer is measured to its end, not to its first byte. Answers: which "
    "endpoint is slow, and since when.",
    ["method", "route"],
    buckets=_HTTP_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge(
    f"{_PREFIX}http_requests_in_flight",
    "Requests being handled right now. Answers: is the process saturated or hung "
    "(rising with flat throughput), independent of how slow each request is.",
    multiprocess_mode="livesum",
)
HTTP_ERRORS = Counter(
    f"{_PREFIX}http_errors_total",
    "Error responses by ORBIT error code. Answers: WHICH failure is happening "
    "(STORAGE_UNAVAILABLE vs GENERATION_FAILED vs INTERNAL_ERROR), which a bare "
    "5xx count cannot say.",
    ["code"],
)

# -- Database ------------------------------------------------------------------

DB_QUERY_DURATION = Histogram(
    f"{_PREFIX}db_query_duration_seconds",
    "Statement execution time, by statement type. Answers: is the database slow, "
    "and is it reads or writes.",
    ["operation", "outcome"],
    buckets=_DB_BUCKETS,
)
DB_ERRORS = Counter(
    f"{_PREFIX}db_errors_total",
    "Failed statements by cause (statement_timeout, deadlock, connection, other). "
    "Constraint violations are business outcomes and are not counted. Answers: "
    "is the database rejecting work, and why.",
    ["kind"],
)
DB_POOL_CONNECTIONS = Gauge(
    f"{_PREFIX}db_pool_connections",
    "Pooled connections by state (in_use, idle). Answers: is the pool saturated -- "
    "the usual reason for a latency cliff with a healthy database.",
    ["state"],
    multiprocess_mode="livesum",
)
DB_POOL_LIMIT = Gauge(
    f"{_PREFIX}db_pool_limit",
    "Maximum connections this process may open (pool_size + max_overflow). "
    "Answers: how close the pool is to saturation, as in_use divided by this.",
    multiprocess_mode="livesum",
)

# -- Queue and processing ------------------------------------------------------

QUEUE_PUBLISH = Counter(
    f"{_PREFIX}queue_publish_total",
    "Job publishes to the broker after the job row committed (ok, failed). "
    "Answers: is the broker reachable from the API. A failed publish delays a "
    "job until recovery re-publishes it; it never loses it.",
    ["outcome"],
)
QUEUE_JOBS = Gauge(
    f"{_PREFIX}queue_jobs",
    "Processing jobs by state, read from PostgreSQL (the source of truth, not the "
    "broker): ready (queued and due), scheduled (queued for a later retry), "
    "running, lease_expired (running but abandoned -- a dead worker's job awaiting "
    "recovery). Answers: is there a backlog, and are jobs stuck.",
    ["state"],
    multiprocess_mode="livemax",
)
QUEUE_OLDEST_READY_AGE = Gauge(
    f"{_PREFIX}queue_oldest_ready_job_age_seconds",
    "Age of the oldest job that is due but not started. Answers: how long does a "
    "document wait for a worker -- the single best worker-capacity signal.",
    multiprocess_mode="livemax",
)
JOB_QUEUE_WAIT = Histogram(
    f"{_PREFIX}job_queue_wait_seconds",
    "Time between a job becoming due and a worker claiming it. Answers: the "
    "latency users experience from queueing, over time.",
    buckets=_WAIT_BUCKETS,
)
JOB_EXECUTIONS = Counter(
    f"{_PREFIX}job_executions_total",
    "Worker executions by outcome (succeeded, retry_scheduled, failed, skipped, "
    "lease_lost). Answers: is the pipeline succeeding, and is it retrying a lot.",
    ["outcome"],
)
PROCESSING_FAILURES = Counter(
    f"{_PREFIX}processing_failures_total",
    "Failed processing attempts by error code and kind (transient, permanent, "
    "defect); terminal=true when the document gave up. Answers: why documents "
    "fail. A defect is a bug and pages.",
    ["error_code", "failure_kind", "terminal"],
)
JOB_RECOVERIES = Counter(
    f"{_PREFIX}job_recoveries_total",
    "Jobs recovery had to rescue: abandoned (a worker died holding it) or "
    "redelivered (the broker lost the message). Answers: is the worker fleet "
    "unstable, is the broker dropping messages.",
    ["kind"],
)
TASK_OUTCOME_NOT_RECORDED = Counter(
    f"{_PREFIX}task_outcome_not_recorded_total",
    "Tasks that ran but could not write their outcome (database unreachable from "
    "the worker). Answers: is the worker blind. Pages.",
)
DOCUMENT_PROCESSING_DURATION = Histogram(
    f"{_PREFIX}document_processing_duration_seconds",
    "One worker execution, claim to outcome, by document format. Answers: has "
    "processing gotten slower, and for which format.",
    ["format", "outcome"],
    buckets=_STAGE_BUCKETS,
)
DOCUMENT_READY_LATENCY = Histogram(
    f"{_PREFIX}document_ready_latency_seconds",
    "Upload accepted to READY, including queueing and retries. Answers: what a "
    "user waits for -- the end-to-end number the SLO is written against.",
    buckets=_WAIT_BUCKETS,
)
PIPELINE_STAGE_DURATION = Histogram(
    f"{_PREFIX}pipeline_stage_duration_seconds",
    "One pipeline stage (fetch, parse, normalize, chunk, embed, index). Answers: "
    "where processing time goes.",
    ["stage", "outcome"],
    buckets=_STAGE_BUCKETS,
)

# -- Retrieval and answering ---------------------------------------------------

SEARCH_DURATION = Histogram(
    f"{_PREFIX}search_duration_seconds",
    "A search inside the use case (excludes HTTP). Answers: is search slow, and "
    "in which mode. outcome: ok, degraded (semantic half unavailable), error.",
    ["mode", "outcome"],
    buckets=_HTTP_BUCKETS,
)
SEARCH_STAGE_DURATION = Histogram(
    f"{_PREFIX}search_stage_duration_seconds",
    "One retrieval stage: query_embedding, lexical, semantic, hydrate. Answers: "
    "WHICH part of a slow search is slow -- the provider, the full-text scan, "
    "the vector index, or loading results.",
    ["stage"],
    buckets=_HTTP_BUCKETS,
)
SEARCH_DEGRADED = Counter(
    f"{_PREFIX}search_degraded_total",
    "Searches answered from one retriever because the other failed. Answers: how "
    "much traffic is silently getting worse results.",
    ["reason"],
)
RAG_ANSWERS = Counter(
    f"{_PREFIX}rag_answers_total",
    "Answers by terminal status, stop reason, and grounding. Answers: are "
    "questions being answered, failing, timing out, or coming back uncited.",
    ["status", "stop_reason", "grounding"],
)
RAG_DURATION = Histogram(
    f"{_PREFIX}rag_duration_seconds",
    "One answer by stage: retrieval, first_token, generation, total. Answers: is a "
    "slow answer slow to find sources, slow to start, or slow to finish.",
    ["stage"],
    buckets=_HTTP_BUCKETS,
)
CITATIONS = Counter(
    f"{_PREFIX}citations_total",
    "Citations the model produced, resolved or discarded (ADR-0006). Answers: has "
    "the model, prompt, or handle format regressed -- a rising discarded share "
    "means answers are citing sources that do not exist.",
    ["result"],
)

# -- AI providers --------------------------------------------------------------

AI_REQUEST_DURATION = Histogram(
    f"{_PREFIX}ai_request_duration_seconds",
    "One provider HTTP call (not one logical operation: a retried request is "
    "several), by operation and outcome. Answers: is the provider slow, "
    "rate-limiting, or down. Retry backoff sleeps are excluded on purpose.",
    ["provider", "operation", "outcome"],
    buckets=_AI_BUCKETS,
)
AI_TOKENS = Counter(
    f"{_PREFIX}ai_tokens_total",
    "Tokens the provider reported using (input, output). Answers: what is this "
    "costing, and what drove the change.",
    ["operation", "kind"],
)
AI_RETRIES = Counter(
    f"{_PREFIX}ai_retries_total",
    "In-process retries by reason. Answers: is the provider degrading -- retries "
    "rise before failures do.",
    ["operation", "reason"],
)
LLM_FIRST_TOKEN = Histogram(
    f"{_PREFIX}llm_first_token_seconds",
    "Time to the first streamed token. Answers: how long a user stares at an empty answer.",
    buckets=_AI_BUCKETS,
)
LLM_CIRCUIT_OPEN = Gauge(
    f"{_PREFIX}llm_circuit_open",
    "1 while the language-model circuit breaker is refusing calls. Answers: are "
    "questions being failed fast because the provider is down.",
    multiprocess_mode="livemax",
)

# -- Cache and rate limiting ---------------------------------------------------

CACHE_REQUESTS = Counter(
    f"{_PREFIX}cache_requests_total",
    "Cache lookups by cache and result (hit, miss, error). Answers: is the cache "
    "earning its keep, and is Redis failing open (error).",
    ["cache", "result"],
)
RATE_LIMIT_DECISIONS = Counter(
    f"{_PREFIX}rate_limit_decisions_total",
    "Rate-limit decisions by scope (allowed, rejected, unavailable). "
    "'unavailable' means Redis was down and the limiter FAILED OPEN -- "
    "brute-force protection is absent while it is nonzero. Answers: is anyone "
    "hammering login, and is protection working.",
    ["scope", "decision"],
)

# -- Object storage ------------------------------------------------------------

STORAGE_DURATION = Histogram(
    f"{_PREFIX}storage_operation_duration_seconds",
    "Object-storage operations by outcome (ok, not_found, error). Answers: is "
    "storage the reason uploads or downloads are slow or failing.",
    ["operation", "outcome"],
    buckets=_STORAGE_BUCKETS,
)

# -- Dependency health ---------------------------------------------------------

DEPENDENCY_UP = Gauge(
    f"{_PREFIX}dependency_up",
    "1 if the last readiness check of the dependency passed. Answers: which "
    "dependency is down, as a time series an alert can watch, without scraping "
    "/readyz.",
    ["dependency"],
    multiprocess_mode="livemostrecent",
)
DEPENDENCY_CHECK_DURATION = Histogram(
    f"{_PREFIX}dependency_check_duration_seconds",
    "Readiness probe latency. Answers: is a dependency slow before it is down.",
    ["dependency"],
    buckets=_DEPENDENCY_BUCKETS,
)


# -- Helpers -------------------------------------------------------------------


def bounded(value: str | None, allowed: Collection[str], *, default: str = "other") -> str:
    """Return ``value`` if it is one of ``allowed``, else ``default``.

    The guard between a value that came from outside the process and a metric
    label. Without it, a caller who controls the value controls the number of
    time series.
    """
    return value if value is not None and value in allowed else default


@dataclass(slots=True)
class Outcome:
    """Handed to the body of :func:`observe`; the body may set the outcome."""

    value: str = "ok"


@contextmanager
def observe(
    histogram: Histogram,
    *,
    classify: Callable[[BaseException], str] | None = None,
    **labels: str,
) -> Iterator[Outcome]:
    """Time a block into ``histogram``, labelling the result ``outcome``.

    The outcome is ``"ok"`` unless the body sets it or raises. A raised
    exception is labelled by ``classify`` (``"error"`` when none is given) and
    re-raised untouched -- a metric must never change control flow.
    """
    outcome = Outcome()
    began = time.perf_counter()
    try:
        yield outcome
    except BaseException as exc:
        outcome.value = classify(exc) if classify is not None else "error"
        raise
    finally:
        histogram.labels(**labels, outcome=outcome.value).observe(time.perf_counter() - began)


@contextmanager
def observe_duration(histogram: Histogram, **labels: str) -> Iterator[None]:
    """Time a block into a histogram that has no outcome label."""
    began = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(**labels).observe(time.perf_counter() - began)


# -- Exposition ----------------------------------------------------------------

MULTIPROC_ENV: Final = "PROMETHEUS_MULTIPROC_DIR"


def multiprocess_enabled() -> bool:
    return bool(os.environ.get(MULTIPROC_ENV))


def serving_registry() -> CollectorRegistry:
    """The registry a metrics endpoint should expose.

    With ``PROMETHEUS_MULTIPROC_DIR`` set, a fresh registry that merges every
    process's samples from that directory; otherwise the process-global one.
    The variable must be set *before* this module is imported --
    ``prometheus_client`` picks its value backend at import time -- which is why
    it is an environment variable of the container, not a setting.
    """
    if multiprocess_enabled():
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return registry
    return REGISTRY


def mark_process_dead(pid: int) -> None:
    """Retire a dead child's gauges from a multiprocess scrape."""
    if multiprocess_enabled():
        multiprocess.mark_process_dead(pid)  # type: ignore[no-untyped-call]
