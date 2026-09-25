"""Conversations and grounded question answering.

Two ways to ask, one implementation behind them:

* `POST .../messages` answers in one response. Failures are ordinary error
  envelopes: `RETRIEVAL_FAILED`, `GENERATION_FAILED` (and `_TIMEOUT`,
  `_RATE_LIMITED`), `CONVERSATION_UNAVAILABLE`.
* `POST .../messages/stream` answers as Server-Sent Events. Everything that
  can fail *before* generation -- validation, a conversation that is not the
  caller's, another answer in flight, retrieval -- still fails as a plain HTTP
  error, because the stream is only opened once evidence is in hand. After
  that, failures arrive as an `error` event.

Stream events, in order: `started`, `retrieval`, any number of `delta`, then
exactly one of `done` or `error`. `delta` text is provisional; the `done`
message is authoritative -- its citations are resolved and any handle that
matched no retrieved source has been removed from its content.

POST rather than GET for the stream: the question is user content, and a
query string ends up in access logs and browser history.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orbit.api.deps import (
    AccessContextDep,
    AnswerQuestionDep,
    ClientIpDep,
    CreateConversationDep,
    DeleteConversationDep,
    GetConversationDep,
    ListConversationsDep,
    ListMessagesDep,
)
from orbit.api.v1.schemas.conversations import (
    AskRequest,
    ConversationOut,
    CreateConversationRequest,
    DeltaEventOut,
    ErrorEventOut,
    MessageListOut,
    MessageOut,
    RetrievalEventOut,
    StartedEventOut,
)
from orbit.api.v1.schemas.pagination import PageLimit, PageResponse
from orbit.application.answering.answer_question import (
    AnswerCompleted,
    AnswerDelta,
    AnswerEvent,
    AnswerFailed,
    AnswerQuestion,
    AnswerStarted,
    PreparedAnswer,
    RetrievalCompleted,
)
from orbit.core.logging import bind_correlation, get_logger
from orbit.domain.errors import OrbitError

logger = get_logger(__name__)

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["conversations"])

_INTERNAL_MESSAGE = "An unexpected error occurred. Quote the request id if you report this."


def _request_id(request: Request) -> str:
    request_id: str = getattr(request.state, "request_id", "")
    return request_id


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationOut,
    summary="Start a conversation",
)
async def create_conversation(
    body: CreateConversationRequest, ctx: AccessContextDep, create: CreateConversationDep
) -> ConversationOut:
    return ConversationOut.from_domain(await create.execute(ctx, title=body.title))


@router.get(
    "",
    response_model=PageResponse[ConversationOut],
    summary="List my conversations",
    description="The caller's own conversations in this workspace, newest first.",
)
async def list_conversations(
    ctx: AccessContextDep,
    list_conversations: ListConversationsDep,
    limit: PageLimit = 25,
    cursor: str | None = None,
) -> PageResponse[ConversationOut]:
    page = await list_conversations.execute(ctx, limit=limit, cursor=cursor)
    return PageResponse[ConversationOut](
        items=[ConversationOut.from_domain(c) for c in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/{conversation_id}", response_model=ConversationOut, summary="Get a conversation")
async def get_conversation(
    conversation_id: uuid.UUID, ctx: AccessContextDep, get: GetConversationDep
) -> ConversationOut:
    return ConversationOut.from_domain(await get.execute(ctx, conversation_id))


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
async def delete_conversation(
    conversation_id: uuid.UUID, ctx: AccessContextDep, delete: DeleteConversationDep
) -> None:
    await delete.execute(ctx, conversation_id)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessageListOut,
    summary="List a conversation's messages",
    description="In thread order, with citations. Pass `next_after` back as `after`.",
)
async def list_messages(
    conversation_id: uuid.UUID,
    ctx: AccessContextDep,
    list_messages: ListMessagesDep,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: PageLimit = 50,
) -> MessageListOut:
    messages = await list_messages.execute(ctx, conversation_id, after_ordinal=after, limit=limit)
    return MessageListOut(
        items=[MessageOut.from_domain(m) for m in messages],
        next_after=messages[-1].ordinal if len(messages) == limit else None,
    )


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageOut,
    summary="Ask a question",
    description=(
        "Retrieves evidence from the workspace, answers from it, and returns the "
        "assistant message with citations resolved against the retrieved chunks."
    ),
)
async def ask(
    conversation_id: uuid.UUID,
    body: AskRequest,
    request: Request,
    ctx: AccessContextDep,
    answer: AnswerQuestionDep,
    client_ip: ClientIpDep,
) -> MessageOut:
    message = await answer.execute(
        ctx,
        conversation_id,
        body.question,
        document_ids=body.document_ids,
        request_id=_request_id(request),
        client_ip=client_ip,
    )
    return MessageOut.from_domain(message)


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Ask a question, streaming the answer",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def ask_streaming(
    conversation_id: uuid.UUID,
    body: AskRequest,
    request: Request,
    ctx: AccessContextDep,
    answer: AnswerQuestionDep,
    client_ip: ClientIpDep,
) -> StreamingResponse:
    request_id = _request_id(request)
    # Before the stream opens, so these failures are ordinary HTTP errors.
    prepared = await answer.prepare(
        ctx,
        conversation_id,
        body.question,
        document_ids=body.document_ids,
        request_id=request_id,
        client_ip=client_ip,
    )
    return StreamingResponse(
        _event_stream(answer, prepared, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Proxies that buffer would turn a stream back into one response.
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(
    answer: AnswerQuestion, prepared: PreparedAnswer, request_id: str
) -> AsyncIterator[bytes]:
    # The body is produced after the request middleware has returned and
    # cleared its log context; restore it so every line carries the id.
    bind_correlation(request_id=request_id)
    events = answer.events(prepared, stream=True)
    try:
        async for event in events:
            yield _encode(event, request_id)
    except Exception:
        # A defect escaping the use case. The client still gets a terminal
        # event rather than a stream that simply stops.
        logger.exception("answer.stream_failed")
        yield _sse(
            "error",
            ErrorEventOut(
                code="INTERNAL_ERROR",
                message=_INTERNAL_MESSAGE,
                request_id=request_id,
                retryable=False,
            ),
        )
    finally:
        # If the client went away, this closes the use case's generator at
        # once, so it records the partial answer now rather than whenever
        # the garbage collector gets to it.
        await events.aclose()


def _encode(event: AnswerEvent, request_id: str) -> bytes:
    match event:
        case AnswerStarted():
            return _sse(
                "started",
                StartedEventOut(
                    conversation_id=event.conversation_id,
                    question_message_id=event.question.id,
                    answer_message_id=event.answer_message_id,
                ),
            )
        case RetrievalCompleted():
            return _sse(
                "retrieval",
                RetrievalEventOut(
                    retrieved=event.retrieved,
                    sources=event.sources,
                    degraded=event.degraded,
                    retrieval_ms=event.retrieval_ms,
                ),
            )
        case AnswerDelta():
            return _sse("delta", DeltaEventOut(text=event.text))
        case AnswerCompleted():
            return _sse("done", MessageOut.from_domain(event.message))
        case AnswerFailed():
            return _sse("error", _error_event(event, request_id))


def _error_event(event: AnswerFailed, request_id: str) -> ErrorEventOut:
    error = event.error
    answer = MessageOut.from_domain(event.message) if event.message else None
    if not isinstance(error, OrbitError):
        logger.error("answer.stream_defect", exc_info=error)
        return ErrorEventOut(
            code="INTERNAL_ERROR",
            message=_INTERNAL_MESSAGE,
            request_id=request_id,
            retryable=False,
            answer=answer,
        )
    retry_after = error.context.get("retry_after_seconds")
    return ErrorEventOut(
        code=error.code,
        message=error.message,
        request_id=request_id,
        retryable=error.retryable,
        retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
        answer=answer,
    )


def _sse(event: str, payload: BaseModel) -> bytes:
    # `model_dump_json` emits no raw newlines, so one `data:` line suffices.
    return f"event: {event}\ndata: {payload.model_dump_json()}\n\n".encode()
