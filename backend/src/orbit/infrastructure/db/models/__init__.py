"""ORM models.

Importing this package registers every model against ``Base.metadata``, which is
what makes Alembic autogenerate able to see the full schema. Each model is
re-exported so that ``from orbit.infrastructure.db.models import Document`` works
without callers needing to know which module a table lives in.
"""

from orbit.infrastructure.db.models.audit import AuditLog
from orbit.infrastructure.db.models.base import Base, metadata
from orbit.infrastructure.db.models.content import (
    Chunk,
    Document,
    DocumentProcessingJob,
    DocumentTag,
    DocumentVersion,
    FailureKind,
    Folder,
    JobStatus,
    ProcessingStatus,
    Tag,
)
from orbit.infrastructure.db.models.conversation import (
    AnswerGrounding,
    Conversation,
    Message,
    MessageCitation,
    MessageRole,
    MessageStatus,
)
from orbit.infrastructure.db.models.identity import (
    AccountToken,
    AccountTokenPurpose,
    RefreshToken,
    User,
    Workspace,
    WorkspaceMember,
)

__all__ = [
    "AccountToken",
    "AccountTokenPurpose",
    "AnswerGrounding",
    "AuditLog",
    "Base",
    "Chunk",
    "Conversation",
    "Document",
    "DocumentProcessingJob",
    "DocumentTag",
    "DocumentVersion",
    "FailureKind",
    "Folder",
    "JobStatus",
    "Message",
    "MessageCitation",
    "MessageRole",
    "MessageStatus",
    "ProcessingStatus",
    "RefreshToken",
    "Tag",
    "User",
    "Workspace",
    "WorkspaceMember",
    "metadata",
]
