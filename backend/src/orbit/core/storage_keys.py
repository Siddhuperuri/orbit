"""Object storage key generation (ADR-0011).

A storage key is built **only** from server-generated identifiers -- a
workspace id, a document id, a version id, each already a `UUID` before this
module ever sees it. No function here accepts a filename, an extension, or any
other string a caller supplied. That is what makes path traversal
unrepresentable rather than merely rejected: there is no code path through
which attacker-controlled bytes could become a path segment, so there is
nothing for a `../` to traverse out of.

Deliberately in `core`, not `domain`: this is a pure string-formatting utility
with no business rules and no dependency on anything, exactly like
`core/ids.py`. Both `application` (to build a key before an upload) and
`infrastructure` (to recognise ORBIT's own keys during the orphan sweep) need
it, and `core` sits below both in the layering (`backend/.importlinter`).
"""

from __future__ import annotations

import uuid

_PREFIX = "workspaces"
_SEGMENT = "documents"


def document_version_key(
    workspace_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
) -> str:
    """The key for one document version's stored object.

    Every component is a UUID already validated as such by the type system --
    there is no `str` parameter here for a caller to smuggle anything through.
    The version id, not the content hash, makes the key unique: the hash is
    only known once the upload stream has been fully read, but the key must
    exist before the first byte is written to storage (ADR-0011's "storage
    first" ordering), so it cannot depend on a value that does not exist yet.
    """
    return f"{_PREFIX}/{workspace_id}/{_SEGMENT}/{document_id}/{version_id}"


def workspace_prefix(workspace_id: uuid.UUID) -> str:
    """Every key ORBIT could have written for one workspace.

    Used to scope the orphan sweep to a single tenant's objects, and nothing
    else -- a sweep is a maintenance operation with the same tenant boundary
    as every other repository call.
    """
    return f"{_PREFIX}/{workspace_id}/{_SEGMENT}/"
