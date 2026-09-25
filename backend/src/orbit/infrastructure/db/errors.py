"""Translating driver errors into domain errors.

Extracted from `unit_of_work.py` into its own module specifically to avoid a
circular import: `unit_of_work.py` imports every `Sql*Repository` to assemble
the `UnitOfWork`, and `SqlDocumentRepository` needs this same translation
applied to a flush it issues mid-method (see its `create`/`add_version`
docstrings) -- so the translator cannot live in the module that imports the
repository, or the two would import each other.

Constraint names are deterministic (the naming convention in
`infrastructure/db/models/base.py`), which is what makes this mapping
possible. Without it, every violation would surface as a generic 500 and the
caller would have no way to tell "this name is taken" from "the database is
broken".
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.logging import get_logger
from orbit.domain.errors import ConflictError

logger = get_logger(__name__)

_CONSTRAINT_MESSAGES: dict[str, str] = {
    "uq_users_email_lower": "An account with that email address already exists.",
    "uq_workspaces_slug_lower": "That workspace URL is already taken.",
    "uq_tags_workspace_id_name_lower": "A tag with that name already exists.",
    "uq_folders_parent_name": "A folder with that name already exists here.",
    "uq_document_versions_workspace_content_current": (
        "That file has already been uploaded to this workspace."
    ),
    "uq_document_versions_current": "This document already has a current version.",
    "uq_jobs_active_per_version": "This document is already being processed.",
    # A second "first" job for a version collides on the attempt number before
    # the active-job index is even consulted; to a caller it is the same fact.
    "uq_jobs_document_version_id_attempt": "This document is already being processed.",
    "uq_refresh_tokens_token_hash": "That token has already been issued.",
}


async def flush_translating_conflicts(session: AsyncSession) -> None:
    """Flush, turning a constraint violation into a domain `ConflictError`.

    Every repository flushes through this rather than calling
    ``session.flush()`` itself. A raw `IntegrityError` escaping a repository
    reaches the error handler as an unrecognised exception and is answered as a
    500 -- so a duplicate email, a name already taken, or a re-uploaded file
    would be reported to the user as "something went wrong on our end" instead
    of the thing they can actually fix. The unit of work translates at
    ``commit()``, but a repository that flushes early (to attribute the
    violation to its own statement) bypasses that, which is exactly the case
    this exists for.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        raise translate_integrity_error(exc) from exc


def translate_integrity_error(exc: IntegrityError) -> ConflictError:
    """Turn a driver error into a domain error with a user-safe message.

    The original exception carries the SQL statement, the parameter values, and
    the schema. None of that may reach a client, so it stops here and travels
    onward only as structured log context.
    """
    detail = str(getattr(exc.orig, "__cause__", exc.orig) or exc.orig)

    for constraint, message in _CONSTRAINT_MESSAGES.items():
        if constraint in detail:
            return ConflictError(message, constraint=constraint)

    logger.warning("db.unmapped_integrity_error", constraint_detail=detail[:200])
    return ConflictError("The request conflicts with existing data.")
