"""Conversations, messages, and citations as the domain sees them.

A conversation is a personal thread inside one workspace: owned by the user
who started it, invisible to other members (see the table's docstring in
`infrastructure/db/models/conversation.py`).

**An assistant message is written twice, exactly.** It is inserted `PENDING`
when the question is accepted -- which reserves its place in the thread before
any slow work starts -- and moved once to a terminal state (`COMPLETE`,
`PARTIAL`, `FAILED`) when the answer ends, however it ends. The transition is
conditional on the row still being `PENDING`, so a message is never rewritten
after it has been shown to anyone, and a crashed request leaves a row that can
be recognised and closed rather than a gap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from orbit.domain.errors import ValidationError

MAX_TITLE_LENGTH = 512
DEFAULT_CONVERSATION_TITLE = "New conversation"
#: Mirrors `message_citations.snippet`'s width.
MAX_CITATION_SNIPPET_CHARS = 2000


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    #: Accepted; the answer is being produced.
    PENDING = "pending"
    #: The model finished its answer.
    COMPLETE = "complete"
    #: Some text was produced, but the answer did not finish -- cancelled,
    #: truncated, timed out, or the provider failed mid-stream.
    PARTIAL = "partial"
    #: No usable answer.
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self is not MessageStatus.PENDING


class Grounding(StrEnum):
    """How well an answer is supported by the workspace's documents.

    Presented to the user, not hidden: an uncited answer is rendered as
    visibly weaker than a cited one (ADR-0006).
    """

    #: At least one claim cites a retrieved source.
    GROUNDED = "grounded"
    #: The model answered but cited nothing that resolved.
    UNCITED = "uncited"
    #: Sources were retrieved, and the model said they do not answer the
    #: question.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: Retrieval found nothing to answer from; the model was not asked.
    NO_EVIDENCE = "no_evidence"


class StopReason(StrEnum):
    COMPLETED = "completed"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTERED = "content_filtered"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_RESPONSE = "malformed_response"
    RETRIEVAL_FAILED = "retrieval_failed"
    #: The request that owned a PENDING message died without closing it.
    ABANDONED = "abandoned"


class FailureStage(StrEnum):
    """Which half of the pipeline failed. Never both, never ambiguous."""

    RETRIEVAL = "retrieval"
    GENERATION = "generation"


def normalize_title(raw: str | None) -> str:
    if raw is None:
        return DEFAULT_CONVERSATION_TITLE
    title = " ".join(raw.split())
    if not title:
        msg = "A conversation title cannot be blank."
        raise ValidationError(msg)
    if len(title) > MAX_TITLE_LENGTH:
        msg = f"A conversation title is limited to {MAX_TITLE_LENGTH} characters."
        raise ValidationError(msg, length=len(title))
    return title


@dataclass(frozen=True, slots=True)
class Conversation:
    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Citation:
    """A retrieved chunk that an answer cites -- a snapshot taken when the
    answer was written, so it still renders after the chunk is rebuilt.

    Every field is copied from the chunk's database record. None comes from
    model output: the model contributes only which *handle* it used.
    """

    handle: str
    ordinal: int
    document_id: uuid.UUID
    document_title: str
    #: `None` once the version has been purged; the snapshot remains.
    document_version_id: uuid.UUID | None
    version_number: int | None
    #: `None` once the chunk has been rebuilt by reprocessing.
    chunk_id: uuid.UUID | None
    chunk_ordinal: int | None
    page_from: int | None
    page_to: int | None
    heading_path: str | None
    char_start: int | None
    char_end: int | None
    snippet: str


@dataclass(frozen=True, slots=True)
class AnswerMetrics:
    """Measured per answer, persisted with it, and logged."""

    retrieval_ms: int | None = None
    generation_ms: int | None = None
    total_ms: int | None = None
    #: Time from the start of generation to the first streamed text.
    first_token_ms: int | None = None
    #: Candidates retrieval returned, before context construction.
    retrieved_count: int | None = None
    #: Estimated tokens of source text placed in the prompt.
    context_tokens: int | None = None
    #: Set when retrieval ran in a degraded mode (e.g. lexical only).
    retrieval_degraded: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: uuid.UUID
    conversation_id: uuid.UUID
    workspace_id: uuid.UUID
    role: MessageRole
    ordinal: int
    content: str
    status: MessageStatus
    created_at: datetime
    model_id: str | None = None
    prompt_version: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    grounding: Grounding | None = None
    stop_reason: StopReason | None = None
    failure_stage: FailureStage | None = None
    failure_code: str | None = None
    discarded_citation_count: int = 0
    metrics: AnswerMetrics | None = None
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class StartedTurn:
    """The two rows written when a question is accepted."""

    question: ChatMessage
    #: The PENDING assistant message the answer will be written into.
    answer: ChatMessage


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """Everything the single PENDING -> terminal transition writes."""

    status: MessageStatus
    content: str
    stop_reason: StopReason
    grounding: Grounding | None
    failure_stage: FailureStage | None
    failure_code: str | None
    model_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    discarded_citation_count: int
    metrics: AnswerMetrics
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not self.status.is_terminal:
            msg = "A finished answer must have a terminal status."
            raise ValueError(msg)
        if self.status is MessageStatus.FAILED and self.failure_code is None:
            msg = "A failed answer must say why."
            raise ValueError(msg)
