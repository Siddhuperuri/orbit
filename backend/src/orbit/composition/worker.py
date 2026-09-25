"""Celery worker entrypoint.

Run it either way:

    celery --app orbit.composition.worker:celery_app worker --pool=prefork
    python -m orbit.composition.worker worker --pool=solo
    python -m orbit.composition.worker recover        # one recovery sweep, then exit
    python -m orbit.composition.worker index-status   # embedding coverage report
    python -m orbit.composition.worker reindex-embeddings [--max-chunks N]

The worker runs in a separate process from the API (ADR-0001) because document
parsing is long-running, CPU-bound, and processes untrusted input. It shares the
same package and the same configuration, so the two can never drift apart in
what they believe the schema or the settings to be.

This module is wiring only. The tasks are thin: parse the payload, hand it to
`WorkerRuntime`, and decide what Celery should do with an exception the
pipeline could not record. Everything that matters is testable without Celery.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from celery import Celery, Task
from celery.signals import setup_logging, worker_process_shutdown, worker_ready

from orbit import __version__
from orbit.composition.metrics_server import MetricsServer
from orbit.composition.worker_container import WorkerContainer
from orbit.composition.worker_runtime import InvalidTaskPayloadError, WorkerRuntime
from orbit.core.config import get_settings
from orbit.core.logging import configure_logging, get_logger
from orbit.core.metrics import (
    BUILD_INFO,
    TASK_OUTCOME_NOT_RECORDED,
    mark_process_dead,
    multiprocess_enabled,
)
from orbit.infrastructure.queue.celery_app import build_celery_app
from orbit.infrastructure.queue.task_names import (
    PROCESS_DOCUMENT_TASK,
    RECOVER_STALLED_JOBS_TASK,
)

_settings = get_settings()

# Logging is configured at import time rather than in a signal handler: Celery
# emits startup records before any signal fires, and those records matter when
# diagnosing a worker that fails to boot.
configure_logging(_settings)

logger = get_logger(__name__)


@setup_logging.connect
def _keep_orbit_logging(**_: object) -> None:
    """Stop Celery replacing ORBIT's logging configuration.

    Celery configures the root logger itself when the worker starts, unless a
    receiver is connected to this signal. Without this, every record after
    startup is rendered by Celery's formatter -- the structured event printed
    as a Python dict repr instead of JSON -- and log queries on `job_id` or
    `request_id` silently stop matching. Re-applying ORBIT's configuration here
    (rather than doing nothing) also covers the pool re-initialising logging.
    """
    configure_logging(_settings)


celery_app: Celery = build_celery_app(_settings)
runtime = WorkerRuntime(lambda: WorkerContainer.create(_settings, celery_app))

#: Redeliveries of a message whose outcome could not be *recorded* (the
#: database was unreachable). Bounded; past it the job is still safe in the
#: database, and recovery re-publishes it once the grace period passes.
_INFRASTRUCTURE_RETRIES = 8
_INFRASTRUCTURE_RETRY_CAP_SECONDS = 300


@celery_app.task(name=PROCESS_DOCUMENT_TASK, bind=True, max_retries=_INFRASTRUCTURE_RETRIES)
def process_document(
    self: Task[Any, Any], job_id: str, request_id: str | None = None
) -> dict[str, Any]:
    try:
        result = runtime.process(job_id, request_id=request_id)
    except InvalidTaskPayloadError:
        logger.exception("task.invalid_payload", task_id=self.request.id)
        return {"outcome": "rejected"}
    except Exception as exc:
        countdown = min(_INFRASTRUCTURE_RETRY_CAP_SECONDS, 5 * 2**self.request.retries)
        TASK_OUTCOME_NOT_RECORDED.inc()
        logger.exception(
            "task.outcome_not_recorded",
            job_id=job_id,
            request_id=request_id,
            redelivery=self.request.retries + 1,
            countdown_seconds=countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
    return {"job_id": job_id, "outcome": result.outcome.value, "reason": result.reason}


@celery_app.task(name=RECOVER_STALLED_JOBS_TASK)
def recover_stalled_jobs() -> dict[str, int]:
    report = runtime.recover()
    return {
        "abandoned_found": report.abandoned_found,
        "abandoned_recovered": report.abandoned_recovered,
        "redelivered": report.redelivered,
    }


_metrics_server: MetricsServer | None = None


@worker_ready.connect
def _serve_metrics(sender: object = None, **_: object) -> None:
    """Expose the worker's metrics on their own port.

    Runs in the worker's *main* process. Under the prefork pool the children
    do the work, so this is only meaningful with `PROMETHEUS_MULTIPROC_DIR`
    set -- the served registry then merges every child's samples. Without it
    a prefork worker would serve an empty registry, so that combination is
    refused loudly rather than exporting a flat line that looks healthy.
    """
    global _metrics_server  # noqa: PLW0603 -- one server per worker process
    if not _settings.metrics_enabled:
        return
    pool_module = type(getattr(sender, "pool", None)).__module__
    if pool_module.endswith("prefork") and not multiprocess_enabled():
        logger.error(
            "metrics.prefork_requires_multiproc_dir",
            hint="set PROMETHEUS_MULTIPROC_DIR to an empty directory before starting the worker",
        )
        return
    BUILD_INFO.labels(
        service=_settings.service_name,
        version=__version__,
        environment=_settings.env.value,
    ).set(1)
    _metrics_server = MetricsServer(host=_settings.metrics_host, port=_settings.worker_metrics_port)
    _metrics_server.start()


@worker_process_shutdown.connect
def _retire_child_metrics(pid: int | None = None, **_: object) -> None:
    """A dead child's gauges must stop being reported."""
    if pid is not None:
        mark_process_dead(pid)


@worker_ready.connect
def _recover_on_startup(**_: object) -> None:
    """Restarting a crashed worker is itself the recovery.

    Jobs the previous process died holding are found by their expired leases
    and rescheduled now, rather than waiting for the next scheduled sweep --
    or forever, in a deployment that runs no scheduler.
    """
    try:
        report = runtime.recover()
    except Exception:
        # A worker that cannot reach the database at boot should still start:
        # it will process nothing until the database returns, and the
        # scheduled sweep will run the recovery then.
        logger.exception("worker.startup_recovery_failed")
        return
    logger.info(
        "worker.startup_recovery_completed",
        abandoned_recovered=report.abandoned_recovered,
        redelivered=report.redelivered,
    )


logger.info(
    "worker.configured",
    environment=_settings.env.value,
    queue=celery_app.conf.task_default_queue,
    ai_provider=_settings.ai_provider.value,
    embedding_model=_settings.embedding_model,
    embedding_dimensions=_settings.embedding_dimensions,
    task_time_limit_seconds=_settings.processing_task_time_limit_seconds,
    lease_seconds=_settings.processing_lease_seconds,
)


_T = TypeVar("_T")


def _with_container(work: Callable[[WorkerContainer], Awaitable[_T]]) -> _T:
    """Run one administrative operation on a container of its own."""

    async def run() -> _T:
        container = WorkerContainer.create(_settings, celery_app)
        try:
            return await work(container)
        finally:
            await container.aclose()

    return asyncio.run(run())


def _index_status() -> int:
    coverage = _with_container(lambda container: container.index_coverage.execute())
    report = {
        "active_space": coverage.active.key,
        "column_dimensions": coverage.column_dimensions,
        "schema_matches": coverage.schema_matches,
        "indexed_chunks": coverage.indexed_chunks,
        "stale_chunks": coverage.stale_chunks,
        "complete": coverage.is_complete,
        "spaces": [
            {
                "space": usage.space_key,
                "chunks": usage.chunks,
                "versions": usage.versions,
                "oldest_embedded_at": _iso(usage.oldest_embedded_at),
                "newest_embedded_at": _iso(usage.newest_embedded_at),
            }
            for usage in coverage.spaces
        ],
        "chunker_versions": dict(coverage.chunker_versions),
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    # Non-zero when work remains, so a deploy pipeline can gate on it.
    return 0 if coverage.is_complete else 3


def _reindex(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reindex-embeddings")
    parser.add_argument(
        "--max-chunks", type=int, default=None, help="stop after examining this many chunks"
    )
    args = parser.parse_args(argv)
    report = _with_container(
        lambda container: container.reindex_embeddings.execute(max_chunks=args.max_chunks)
    )
    sys.stdout.write(json.dumps(dataclasses.asdict(report), indent=2) + "\n")
    return 0 if report.complete and report.inconsistent == 0 else 3


def _iso(value: Any) -> str | None:  # noqa: ANN401 -- datetime or None
    return value.isoformat() if value is not None else None


def main(argv: list[str]) -> int:
    """`python -m orbit.composition.worker` -- the worker without the Celery CLI."""
    if argv[:1] == ["recover"]:
        report = runtime.recover()
        logger.info(
            "recovery.cli_completed",
            abandoned_found=report.abandoned_found,
            abandoned_recovered=report.abandoned_recovered,
            redelivered=report.redelivered,
        )
        return 0
    if argv[:1] == ["index-status"]:
        return _index_status()
    if argv[:1] == ["reindex-embeddings"]:
        return _reindex(argv[1:])
    # Does not return: Celery's worker exits the process when it stops.
    celery_app.worker_main(argv=argv or ["worker", "--loglevel=INFO"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
