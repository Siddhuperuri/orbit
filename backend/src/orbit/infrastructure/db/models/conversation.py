"""Conversations, messages, and citations.

The citation table is where ADR-0006 becomes storage. A citation may not be
whatever the model wrote: it is a **snapshot of a chunk that was actually
retrieved**, written by the application after resolving the model's opaque
handles against the request-scoped candidate set.

The snapshot matters. Reprocessing a document deletes and rebuilds its chunks,
so a citation that only held a foreign key would lose its meaning the moment the
document was updated -- and a months-old answer would silently stop explaining
itself. The columns here therefore carry enough provenance to render the
citation forever, with a nullable link to the live chunk when one still exists.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orbit.infrastructure.db.models.base import (
    Base,
    OptimisticVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

MAX_SNIPPET_LENGTH = 2000


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    """Mirrors `orbit.domain.conversations.MessageStatus`."""

    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class AnswerGrounding(StrEnum):
    """Mirrors `orbit.domain.conversations.Grounding`."""

    GROUNDED = "grounded"
    UNCITED = "uncited"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_EVIDENCE = "no_evidence"


message_role_enum = SAEnum(
    MessageRole,
    name="message_role",
    values_callable=lambda enum: [member.value for member in enum],
)
message_status_enum = SAEnum(
    MessageStatus,
    name="message_status",
    values_callable=lambda enum: [member.value for member in enum],
)
answer_grounding_enum = SAEnum(
    AnswerGrounding,
    name="answer_grounding",
    values_callable=lambda enum: [member.value for member in enum],
)


class Conversation(
    UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, OptimisticVersionMixin, Base
):
    """A question-and-answer thread within one workspace.

    Owned by the user who started it, not by the workspace at large: a
    conversation is a personal working artefact, and other members do not see it
    by default. Sharing is a later feature and will be a separate grant, not a
    relaxation of this ownership.
    """

    __tablename__ = "conversations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # Cascades: a purged account's private threads go with it. Unlike documents,
    # which belong to the workspace, these belong to the person.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", lazy="raise", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_conversations_workspace_id_id"),
        # "My recent conversations in this workspace" -- the sidebar query.
        Index(
            "ix_conversations_workspace_id_user_id_created_at",
            "workspace_id",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
    )


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One turn in a conversation.

    Append-only in the sense that matters: a message is never edited once it
    has reached a terminal status. An assistant message is inserted `pending`
    when its question is accepted -- reserving its ordinal before any slow
    work starts -- and makes exactly one conditional transition to
    `complete`, `partial`, or `failed` (migration 0005). Re-asking creates a
    new message rather than mutating an old one, which is what keeps a
    citation's context truthful.
    """

    __tablename__ = "messages"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    role: Mapped[MessageRole] = mapped_column(message_role_enum, nullable=False)
    # Ordering within the thread. Explicit rather than relying on created_at,
    # which two messages written in the same millisecond would tie on.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Recorded on assistant turns so an answer-quality regression after a model
    # change can be attributed, and so cost is attributable per workspace.
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # How many handles the model emitted that did not resolve to a retrieved
    # chunk and were therefore discarded. A first-class quality signal: a rising
    # rate means the prompt, the model, or the handle format has regressed
    # (ADR-0006).
    discarded_citation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # -- Answer lifecycle (migration 0005) ------------------------------------
    status: Mapped[MessageStatus] = mapped_column(
        message_status_enum, nullable=False, server_default=text("'complete'")
    )
    grounding: Mapped[AnswerGrounding | None] = mapped_column(answer_grounding_enum, nullable=True)
    # Why generation stopped (`orbit.domain.conversations.StopReason`), and --
    # for a failure -- which stage failed and the public error code. Strings,
    # not enums: they are recorded for diagnosis and never branched on in SQL.
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Versioned so an answer-quality change is attributable to a prompt edit
    # as readily as to a model change.
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # -- Measurements -------------------------------------------------------------
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieved_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_degraded: Mapped[str | None] = mapped_column(String(64), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message", lazy="raise", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_messages_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            name="fk_messages_conversation_within_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "ordinal", name="uq_messages_conversation_id_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint(
            "discarded_citation_count >= 0", name="discarded_citation_count_non_negative"
        ),
        # Only an assistant turn has model metadata; a user turn carrying token
        # counts would mean something has been attributed to the wrong side.
        CheckConstraint(
            "role = 'assistant' OR (model_id IS NULL AND prompt_tokens IS NULL)",
            name="only_assistant_messages_carry_model_metadata",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0", name="prompt_tokens_non_negative"
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="completion_tokens_non_negative",
        ),
        # A question is written once, whole; only answers have a lifecycle.
        CheckConstraint(
            "role = 'assistant' OR (status = 'complete' AND grounding IS NULL)",
            name="user_messages_are_complete",
        ),
        CheckConstraint(
            "status <> 'failed' OR failure_code IS NOT NULL", name="failed_messages_say_why"
        ),
        CheckConstraint(
            "failure_stage IS NULL OR failure_stage IN ('retrieval', 'generation')",
            name="failure_stage_known",
        ),
        # At most one answer in flight per conversation, enforced by the
        # database rather than by the row lock alone.
        Index(
            "uq_messages_one_pending_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )


class MessageCitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A source that an assistant message actually used.

    Written by the application from the retrieved candidate set, never parsed
    from model output. A row existing here is therefore evidence that the chunk
    was retrieved for this question -- which is the guarantee ADR-0006 exists to
    provide.
    """

    __tablename__ = "message_citations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    # The handle the model was shown ("S1"), kept so an answer's inline markers
    # can be matched to these rows when rendering.
    handle: Mapped[str] = mapped_column(String(8), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    # Live link, when the chunk still exists. Reprocessing rebuilds chunks, so
    # this is nullable by design and set to NULL rather than cascading -- losing
    # the link must not delete the citation.
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )

    # Snapshot. Sufficient to render the citation after the chunk is gone.
    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    document_title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Added in migration 0005, so a citation names the version and passage in
    # the terms a reader uses ("v3, section 2.1") even after both are gone.
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snippet: Mapped[str] = mapped_column(String(MAX_SNIPPET_LENGTH), nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations", lazy="raise")

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "message_id"],
            ["messages.workspace_id", "messages.id"],
            name="fk_message_citations_message_within_workspace",
            ondelete="CASCADE",
        ),
        # The cited document must belong to the same workspace as the message.
        # Composite, so a citation can never point across a tenant boundary.
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["documents.workspace_id", "documents.id"],
            name="fk_message_citations_document_within_workspace",
            ondelete="CASCADE",
        ),
        UniqueConstraint("message_id", "handle", name="uq_message_citations_message_id_handle"),
        Index("ix_message_citations_message_id", "message_id"),
        # "Which answers cited this document?" -- the reverse view, used when a
        # document is updated or removed.
        Index("ix_message_citations_workspace_id_document_id", "workspace_id", "document_id"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("length(btrim(snippet)) > 0", name="snippet_not_blank"),
        CheckConstraint(
            "char_start IS NULL OR char_end IS NULL OR char_end > char_start",
            name="char_range_is_forward",
        ),
    )
