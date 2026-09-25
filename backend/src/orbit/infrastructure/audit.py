"""Audit sink adapter.

Writes each event in its **own** transaction, not the caller's. That is the
whole design decision in this file, and it cuts both ways:

* An audit write cannot roll back the action it records. A failed login has
  no transaction of its own to join, and a successful one must not be undone
  because the audit insert hit a constraint.
* Equally, an action that rolls back still leaves its audit record. For
  "login failed" or "permission denied" that is correct -- the attempt
  genuinely happened.

Failures never propagate. An audit table outage must not become an
authentication outage; the event is lost, and that loss is itself logged at
ERROR so the gap is visible rather than silent.
"""

from __future__ import annotations

from orbit.core.logging import get_logger
from orbit.domain.ports.audit import AuditEvent
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)


class DatabaseAuditSink:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def record(self, event: AuditEvent) -> None:
        try:
            async with self._uow_factory() as uow:
                await uow.audit.append(event)
                await uow.commit()
        except Exception:
            # Deliberately broad. Anything at all going wrong here -- a
            # database blip, a serialisation failure, a bug in this code --
            # must not turn the user's action into a 500.
            logger.exception(
                "audit.write_failed",
                audit_action=event.action.value,
                actor_user_id=str(event.actor_user_id) if event.actor_user_id else None,
            )
