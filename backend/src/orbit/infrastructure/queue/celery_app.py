"""Celery application factory.

Every limit below is set explicitly. Celery's defaults are tuned for short,
trusted tasks; ORBIT's tasks are long, CPU-bound, and process untrusted input,
so leaving a default in place here is a decision, not an omission.

Rationale for the prefork pool and these limits: ADR-0002.
"""

from __future__ import annotations

from celery import Celery

from orbit.core.config import Settings
from orbit.infrastructure.queue.task_names import RECOVER_STALLED_JOBS_TASK

# Recycle children periodically to bound the blast radius of any native-library
# state corruption accumulated across tasks.
WORKER_MAX_TASKS_PER_CHILD = 100

#: Document processing. Named explicitly so that latency-sensitive work added
#: later cannot end up queued behind a 15-minute PDF.
DOCUMENTS_QUEUE = "documents"


def build_celery_app(settings: Settings) -> Celery:
    """Build the Celery application.

    The time limits and memory ceiling come from `Settings` (defaults: 900 s
    hard, 840 s soft, 512 MB): the hard limit is enforced by SIGKILL on the
    child, which is the only thing that reliably stops a wedged C-level
    parser; the soft limit is raised inside the task first so the pipeline can
    record FAILED with a reason; the memory ceiling retires a child that a
    pathological document bloated.
    """
    app = Celery("orbit", broker=str(settings.celery_broker_url))

    app.conf.update(
        result_backend=str(settings.celery_result_backend),
        # --- Serialization -------------------------------------------------
        # JSON only. Pickle would allow arbitrary code execution from anything
        # able to write to the broker.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # --- Delivery semantics --------------------------------------------
        # Acknowledge after completion, so a child killed mid-task redelivers.
        # This is what makes idempotent tasks a requirement rather than a nicety.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # With late acks, a task still running when the broker connection
        # drops would otherwise be redelivered to another worker *while it
        # keeps running here*. Fencing makes that safe; cancelling makes it
        # rare.
        worker_cancel_long_running_tasks_on_connection_loss=True,
        # The default of 4 lets one worker reserve four long jobs while another
        # sits idle. With minute-scale tasks that is a serious imbalance.
        worker_prefetch_multiplier=1,
        # --- Limits ---------------------------------------------------------
        task_time_limit=settings.processing_task_time_limit_seconds,
        task_soft_time_limit=settings.processing_task_soft_time_limit_seconds,
        worker_max_memory_per_child=settings.worker_max_memory_per_child_kb,
        worker_max_tasks_per_child=WORKER_MAX_TASKS_PER_CHILD,
        # --- Results ---------------------------------------------------------
        # Job state lives in PostgreSQL, which is the source of truth. The Celery
        # result backend is only for operational inspection, so results expire.
        result_expires=3600,
        task_ignore_result=True,
        # --- Routing ---------------------------------------------------------
        task_default_queue=DOCUMENTS_QUEUE,
        task_track_started=True,
        # --- Broker resilience -------------------------------------------------
        broker_connection_retry_on_startup=True,
        # Redis is disposable (docs/architecture/system.md); a broker restart
        # must not permanently wedge a worker.
        broker_transport_options={
            "visibility_timeout": settings.processing_task_time_limit_seconds + 60,
            # Bounded, so publishing from the API during a broker outage
            # fails in seconds rather than hanging the request.
            "socket_connect_timeout": 3,
        },
        broker_connection_timeout=3,
        # --- Recovery schedule (run by `celery beat`) ---------------------------
        # Jobs abandoned by dead workers and messages lost by the broker are
        # found from the database, not trusted to the queue (ADR-0019).
        beat_schedule={
            "recover-stalled-processing-jobs": {
                "task": RECOVER_STALLED_JOBS_TASK,
                "schedule": float(settings.processing_recovery_interval_seconds),
                "options": {"expires": float(settings.processing_recovery_interval_seconds)},
            },
        },
        # --- Logging -----------------------------------------------------------
        # ORBIT's structlog configuration owns the root logger (see the
        # `setup_logging` receiver in `orbit.composition.worker`).
        worker_hijack_root_logger=False,
        # --- Time -------------------------------------------------------------
        timezone="UTC",
        enable_utc=True,
    )

    # Tasks are registered explicitly by the worker's composition root
    # (`orbit.composition.worker`), never by autodiscovery: an implicit import
    # scan makes it hard to tell which tasks a worker actually serves, and it
    # would pull the pipeline into every process that merely publishes.
    return app
