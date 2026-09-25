"""Conversation, message, and citation contracts.

A citation is fully structured -- document, version, chunk, location, snippet
-- and every field is copied from the retrieved chunk's database record, never
from model output (ADR-0006). `handle` is what the answer text contains
(`[S1]`), so a client can link each inline marker to its citation.

`grounding` is part of the contract on purpose: an uncited answer must be
rendered as visibly weaker than a cited one, and a client cannot do that if
the distinction is hidden from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orbit.domain.conversations import (
    MAX_TITLE_LENGTH,
    ChatMessage,
    Citation,
    Conversation,
    FailureStage,
    Grounding,
    MessageRole,
    MessageStatus,
    StopReason,
)
from orbit.domain.retrieval import MAX_DOCUMENT_FILTER, MAX_QUERY_CHARACTERS


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE_LENGTH)


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, conversation: Conversation) -> ConversationOut:
        return cls(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)
    #: Answer only from these documents of the workspace. Never widens scope:
    #: an id from another workspace matches nothing.
    document_ids: list[uuid.UUID] | None = Field(
        default=None, min_length=1, max_length=MAX_DOCUMENT_FILTER
    )


class CitedDocumentOut(BaseModel):
    id: uuid.UUID
    title: str


class CitedVersionOut(BaseModel):
    #: `null` if the version has since been purged; the snapshot remains.
    id: uuid.UUID | None
    version_number: int | None


class CitedChunkOut(BaseModel):
    #: `null` once reprocessing has rebuilt the document's chunks.
    id: uuid.UUID | None
    ordinal: int | None


class CitedLocationOut(BaseModel):
    page_from: int | None
    page_to: int | None
    heading_path: str | None
    char_start: int | None
    char_end: int | None


class CitationOut(BaseModel):
    handle: str
    ordinal: int
    document: CitedDocumentOut
    version: CitedVersionOut
    chunk: CitedChunkOut
    location: CitedLocationOut
    snippet: str

    @classmethod
    def from_domain(cls, citation: Citation) -> CitationOut:
        return cls(
            handle=citation.handle,
            ordinal=citation.ordinal,
            document=CitedDocumentOut(id=citation.document_id, title=citation.document_title),
            version=CitedVersionOut(
                id=citation.document_version_id, version_number=citation.version_number
            ),
            chunk=CitedChunkOut(id=citation.chunk_id, ordinal=citation.chunk_ordinal),
            location=CitedLocationOut(
                page_from=citation.page_from,
                page_to=citation.page_to,
                heading_path=citation.heading_path,
                char_start=citation.char_start,
                char_end=citation.char_end,
            ),
            snippet=citation.snippet,
        )


class FailureOut(BaseModel):
    stage: FailureStage | None
    code: str


class UsageOut(BaseModel):
    #: As reported by the provider; `null` when it did not report them.
    prompt_tokens: int | None
    completion_tokens: int | None


class AnswerMetricsOut(BaseModel):
    retrieval_ms: int | None
    generation_ms: int | None
    first_token_ms: int | None
    total_ms: int | None
    retrieved_count: int | None
    context_tokens: int | None
    retrieval_degraded: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    ordinal: int
    content: str
    status: MessageStatus
    created_at: datetime
    #: Assistant messages only.
    grounding: Grounding | None = None
    stop_reason: StopReason | None = None
    failure: FailureOut | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    usage: UsageOut | None = None
    metrics: AnswerMetricsOut | None = None
    citations: list[CitationOut] = Field(default_factory=list)
    #: Handles the model wrote that matched no retrieved source, and were
    #: therefore removed from `content`.
    discarded_citation_count: int = 0

    @classmethod
    def from_domain(cls, message: ChatMessage) -> MessageOut:
        is_answer = message.role is MessageRole.ASSISTANT
        metrics = message.metrics
        return cls(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            ordinal=message.ordinal,
            content=message.content,
            status=message.status,
            created_at=message.created_at,
            grounding=message.grounding,
            stop_reason=message.stop_reason,
            failure=FailureOut(stage=message.failure_stage, code=message.failure_code)
            if message.failure_code
            else None,
            model_id=message.model_id,
            prompt_version=message.prompt_version,
            usage=UsageOut(
                prompt_tokens=message.prompt_tokens, completion_tokens=message.completion_tokens
            )
            if is_answer
            else None,
            metrics=AnswerMetricsOut(
                retrieval_ms=metrics.retrieval_ms,
                generation_ms=metrics.generation_ms,
                first_token_ms=metrics.first_token_ms,
                total_ms=metrics.total_ms,
                retrieved_count=metrics.retrieved_count,
                context_tokens=metrics.context_tokens,
                retrieval_degraded=metrics.retrieval_degraded,
            )
            if metrics is not None
            else None,
            citations=[CitationOut.from_domain(c) for c in message.citations],
            discarded_citation_count=message.discarded_citation_count,
        )


class MessageListOut(BaseModel):
    items: list[MessageOut]
    #: Pass as `after` to fetch the next messages; `null` when there are none.
    next_after: int | None


# -- Streaming events (text/event-stream) -------------------------------------


class StartedEventOut(BaseModel):
    conversation_id: uuid.UUID
    question_message_id: uuid.UUID
    answer_message_id: uuid.UUID


class RetrievalEventOut(BaseModel):
    retrieved: int
    sources: int
    degraded: str | None
    retrieval_ms: int


class DeltaEventOut(BaseModel):
    text: str


class ErrorEventOut(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool
    retry_after_seconds: int | None = None
    #: The answer as recorded -- possibly partial -- if it could be recorded.
    answer: MessageOut | None = None
