# 0002 — Celery with a prefork pool for document processing

- **Status:** Accepted — the worker's database access is amended by [ADR-0019](0019-asynchronous-processing-pipeline.md)
- **Date:** 2026-09-09

## Context

ORBIT's backend is asyncio-based (FastAPI, SQLAlchemy async, asyncpg). The
consistent choice would be an asyncio-native queue such as ARQ, and the code
would read better for it.

The workload argues otherwise. Document processing:

- executes **untrusted input** — users upload the PDFs, and PDF parsers have a
  long history of pathological behaviour on malformed files;
- is **CPU-bound and synchronous** — `pypdf` and every comparable parser are
  blocking C/Python, not awaitable;
- has **unbounded memory characteristics** — a decompression-heavy or
  deeply-nested document can allocate far beyond its file size;
- can **fail to terminate** — malformed cross-reference tables can drive a
  parser into effectively unbounded work.

The controlling question is: *what happens when one document is hostile?*

In a single-process asyncio worker, a blocking parse occupies the event loop.
Nothing else progresses. Heartbeats stop, the job cannot be cancelled — because
cancellation in asyncio is cooperative and blocking C code never yields — and
the only recovery is an external process kill that also destroys every other
in-flight job in that process.

## Decision

**Celery 5 with the prefork pool**, Redis as broker.

Prefork runs each task in a forked child process, which provides three controls
that matter operationally and cannot be replicated in a single-process worker:

| Setting | What it buys |
|---|---|
| `task_time_limit` | Hard limit enforced by **SIGKILL** on the child. Terminates a wedged parser regardless of whether it yields. |
| `task_soft_time_limit` | Raises an exception inside the task first, so the pipeline can record a FAILED state with a reason before the hard kill. |
| `worker_max_memory_per_child` | Retires a child after it exceeds a memory ceiling, containing leaks and one-off allocation spikes. |
| `worker_max_tasks_per_child` | Bounds the blast radius of any native-library state corruption. |

One hostile document kills one child process. Other jobs continue, and the
message is redelivered or dead-lettered according to policy.

## Alternatives considered

**ARQ.** Asyncio-native, small, pleasant with this codebase. Rejected: single
process, cooperative cancellation only. It cannot preempt a blocking parser,
which is the exact failure this system must survive.

**RQ.** Also forks, which addresses isolation. Rejected: weaker retry semantics,
no soft time limit, and materially less operational tooling than Celery.

**Dramatiq.** A genuinely good fit — forking, clean API, sound retry model.
Rejected narrowly: Celery has a larger operational surface area that is already
understood by most engineers who would maintain this, and broader observability
integration. This is the weakest rejection in this ADR and would be a reasonable
decision to revisit.

**Celery with the gevent or threads pool.** Rejected: both discard the process
isolation that motivated this choice, while keeping Celery's overhead.

**Postgres-backed queue using SKIP LOCKED.** Removes Redis from the critical
path and gives transactional enqueue. Genuinely attractive. Rejected for now
because it means hand-building retries, visibility timeouts, scheduling, and
dead-letter handling — meaningful surface area to own and test, in order to
remove an infrastructure component we already need for caching.

## Consequences

- *(Amended by ADR-0019: the worker runs the async stack on one event loop
  per task instead; no synchronous stack was built.)* The worker calls into the
  database through **synchronous** SQLAlchemy sessions. The application therefore maintains two session factories. This is
  real friction and is accepted deliberately; it is the price of process
  isolation.
- Task payloads carry **identifiers only, never document content**. Redis is not
  a document store, and job payloads end up in logs and dashboards.
- Tasks must be idempotent: prefork means a child can be SIGKILLed after doing
  partial work, and the message will then be redelivered.
- `worker_prefetch_multiplier` is set to 1. The default of 4 lets one worker
  reserve four long jobs while another worker sits idle.
- Celery's own configuration is treated as production code: limits are set
  explicitly, never left at defaults, and are documented in
  `docs/operations/worker.md`.
