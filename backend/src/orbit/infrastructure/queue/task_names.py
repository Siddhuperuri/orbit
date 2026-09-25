"""Task names: the contract between the processes that publish and the worker
that consumes. Changing one is a breaking change for messages already queued."""

from __future__ import annotations

PROCESS_DOCUMENT_TASK = "orbit.documents.process"
RECOVER_STALLED_JOBS_TASK = "orbit.documents.recover_stalled_jobs"
