"""Reclaim objects that storage holds but no version row references.

Two of the failure modes in `upload_document.py`'s ordering discussion produce
exactly this: a database commit that failed after a storage write succeeded,
and a losing side of a deduplication race whose cleanup delete itself failed
(a transient storage error at the worst possible moment). Both leave an object
that costs money and helps nobody.

This is deliberately **not** wired to a schedule in this milestone -- there is
no Celery Beat configuration in the codebase yet to wire it to, and adding one
is out of scope for the storage subsystem itself. It is a plain callable,
independently testable, meant to be invoked periodically once that scheduling
exists (see `docs/decisions/0011-object-storage-and-upload.md`'s lifecycle
note).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orbit.core.clock import Clock, SystemClock
from orbit.core.ids import uuid7_timestamp_ms
from orbit.core.logging import get_logger
from orbit.core.storage_keys import workspace_prefix
from orbit.domain.ports.storage import ObjectStorage
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)

#: An upload in progress holds no database row for the span of one request,
#: which is seconds, not minutes. A grace period an order of magnitude beyond
#: the longest plausible in-flight upload is what keeps this sweep from ever
#: racing a request that is still running (ADR-0011).
DEFAULT_GRACE_PERIOD = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class SweepResult:
    objects_examined: int
    objects_deleted: int
    bytes_reclaimed: int


class SweepOrphanedStorage:
    """Deletes objects under a workspace's prefix with no referencing row.

    Scoped to one workspace per call, mirroring every other repository
    operation's tenant boundary -- there is no "sweep everything" entry point,
    only "sweep this workspace," called once per workspace by whatever
    schedules it.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: ObjectStorage,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._clock = clock or SystemClock()

    async def execute(
        self, workspace_id: uuid.UUID, *, grace_period: timedelta = DEFAULT_GRACE_PERIOD
    ) -> SweepResult:
        examined = 0
        deleted = 0
        reclaimed = 0
        cutoff = self._clock.now()

        async with self._uow_factory() as uow:
            async for summary in self._storage.list_keys(workspace_prefix(workspace_id)):
                examined += 1
                if await uow.documents.exists_by_storage_key(summary.key):
                    continue

                # `grace_period` is enforced by key freshness, not by asking
                # storage for the object's own age: every key embeds a UUIDv7
                # version id (`core/storage_keys.py`), and a UUIDv7's leading
                # bits are a millisecond timestamp -- exactly the "when was
                # this written" fact a `list_objects` response would otherwise
                # require a second, per-object call to obtain.
                if not self._old_enough(summary.key, cutoff=cutoff, grace_period=grace_period):
                    continue

                await self._storage.delete(summary.key)
                deleted += 1
                reclaimed += summary.byte_size

        if deleted:
            logger.info(
                "storage.orphans_reclaimed",
                workspace_id=str(workspace_id),
                objects_examined=examined,
                objects_deleted=deleted,
                bytes_reclaimed=reclaimed,
            )
        return SweepResult(
            objects_examined=examined, objects_deleted=deleted, bytes_reclaimed=reclaimed
        )

    @staticmethod
    def _old_enough(key: str, *, cutoff: datetime, grace_period: timedelta) -> bool:
        version_id_str = key.rsplit("/", 1)[-1]
        try:
            timestamp_ms = uuid7_timestamp_ms(uuid.UUID(version_id_str))
        except ValueError:
            # Not a well-formed UUID, or not a v7 one -- either way, not a key
            # `document_version_key` could have produced. Treat it as eligible
            # rather than silently ignoring storage that ORBIT does not
            # recognise as its own: an unrecognised key under ORBIT's own
            # prefix is itself worth reclaiming, not worth protecting.
            return True
        age = cutoff - datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        return age >= grace_period
