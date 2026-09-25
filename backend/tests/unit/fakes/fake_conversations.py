"""In-memory `ConversationRepository`.

Mirrors the SQL adapter's *rules* -- owner-and-workspace scoping, ordinal
assignment, one PENDING answer per conversation, stale-PENDING recovery, and
the conditional PENDING -> terminal transition -- because those rules are
what the answering use case relies on. Row locking and the unique partial
index are covered against PostgreSQL in the integration suite.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext
from orbit.domain.conversations import (
    ChatMessage,
    Conversation,
    FinishedAnswer,
    MessageRole,
    MessageStatus,
    StartedTurn,
    StopReason,
)
from orbit.domain.errors import AnswerInProgressError, NotFoundError
from orbit.domain.models.pagination import Page

if TYPE_CHECKING:
    from tests.unit.fakes.in_memory_unit_of_work import _State


class FakeConversationRepository:
    def __init__(self, state: _State) -> None:
        self._state = state

    def _now(self) -> datetime:
        controls = self._state.controls
        return controls.clock_now() if controls.clock_now else datetime.now(UTC)

    def _owned(self, ctx: AccessContext, conversation_id: uuid.UUID) -> Conversation | None:
        conversation = self._state.conversations.get(conversation_id)
        if (
            conversation is None
            or conversation.id in self._state.deleted_conversations
            or conversation.workspace_id != ctx.workspace_id
            or conversation.user_id != ctx.user_id
        ):
            return None
        return conversation

    def _thread(self, conversation_id: uuid.UUID) -> list[ChatMessage]:
        return sorted(
            (m for m in self._state.messages.values() if m.conversation_id == conversation_id),
            key=lambda m: m.ordinal,
        )

    async def create(self, ctx: AccessContext, *, title: str) -> Conversation:
        now = self._now()
        conversation = Conversation(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._state.conversations[conversation.id] = conversation
        return conversation

    async def get(self, ctx: AccessContext, conversation_id: uuid.UUID) -> Conversation | None:
        return self._owned(ctx, conversation_id)

    async def list_page(self, ctx: AccessContext, *, limit: int, cursor: str | None) -> Page:
        del cursor  # one page is enough for use-case tests
        mine = sorted(
            (c for c in self._state.conversations.values() if self._owned(ctx, c.id) is not None),
            key=lambda c: (c.created_at, c.id),
            reverse=True,
        )
        return Page(items=tuple(mine[:limit]), next_cursor=None)

    async def soft_delete(self, ctx: AccessContext, conversation_id: uuid.UUID) -> bool:
        if self._owned(ctx, conversation_id) is None:
            return False
        self._state.deleted_conversations.add(conversation_id)
        return True

    async def start_turn(  # noqa: PLR0913
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        *,
        question: str,
        model_id: str,
        prompt_version: str,
        request_id: str | None,
        stale_before: datetime,
    ) -> StartedTurn:
        del request_id
        if self._state.controls.fail_conversation_writes is not None:
            raise self._state.controls.fail_conversation_writes
        if self._owned(ctx, conversation_id) is None:
            msg = "Conversation not found."
            raise NotFoundError(msg)
        thread = self._thread(conversation_id)
        for message in thread:
            if message.status is MessageStatus.PENDING:
                if message.created_at >= stale_before:
                    msg = "The previous question in this conversation is still being answered."
                    raise AnswerInProgressError(msg)
                self._state.messages[message.id] = dataclasses.replace(
                    message,
                    status=MessageStatus.FAILED,
                    stop_reason=StopReason.ABANDONED,
                    failure_code="ANSWER_ABANDONED",
                )
        ordinal = thread[-1].ordinal + 1 if thread else 0
        now = self._now()
        asked = ChatMessage(
            id=new_uuid7(),
            conversation_id=conversation_id,
            workspace_id=ctx.workspace_id,
            role=MessageRole.USER,
            ordinal=ordinal,
            content=question,
            status=MessageStatus.COMPLETE,
            created_at=now,
        )
        pending = ChatMessage(
            id=new_uuid7(),
            conversation_id=conversation_id,
            workspace_id=ctx.workspace_id,
            role=MessageRole.ASSISTANT,
            ordinal=ordinal + 1,
            content="",
            status=MessageStatus.PENDING,
            created_at=now,
            model_id=model_id,
            prompt_version=prompt_version,
        )
        self._state.messages[asked.id] = asked
        self._state.messages[pending.id] = pending
        return StartedTurn(question=asked, answer=pending)

    async def finish_answer(
        self, ctx: AccessContext, message_id: uuid.UUID, answer: FinishedAnswer
    ) -> bool:
        if self._state.controls.fail_conversation_writes is not None:
            raise self._state.controls.fail_conversation_writes
        message = self._state.messages.get(message_id)
        if (
            message is None
            or message.status is not MessageStatus.PENDING
            or self._owned(ctx, message.conversation_id) is None
        ):
            return False
        self._state.messages[message_id] = dataclasses.replace(
            message,
            status=answer.status,
            content=answer.content,
            stop_reason=answer.stop_reason,
            grounding=answer.grounding,
            failure_stage=answer.failure_stage,
            failure_code=answer.failure_code,
            model_id=answer.model_id,
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            discarded_citation_count=answer.discarded_citation_count,
            metrics=answer.metrics,
            citations=answer.citations,
        )
        return True

    async def recent_exchanges(
        self, ctx: AccessContext, conversation_id: uuid.UUID, *, before_ordinal: int, limit: int
    ) -> Sequence[ChatMessage]:
        if self._owned(ctx, conversation_id) is None:
            return []
        worth_showing = [
            dataclasses.replace(m, citations=())
            for m in self._thread(conversation_id)
            if m.ordinal < before_ordinal
            and (
                m.role is MessageRole.USER
                or (m.status in (MessageStatus.COMPLETE, MessageStatus.PARTIAL) and m.content)
            )
        ]
        return worth_showing[-limit:] if limit else []

    async def list_messages(
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        *,
        after_ordinal: int | None,
        limit: int,
    ) -> Sequence[ChatMessage]:
        if self._owned(ctx, conversation_id) is None:
            return []
        return [
            m
            for m in self._thread(conversation_id)
            if after_ordinal is None or m.ordinal > after_ordinal
        ][:limit]
