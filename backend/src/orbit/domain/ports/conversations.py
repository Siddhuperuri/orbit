"""The conversation repository port.

Every method takes the caller's `AccessContext` and scopes by **workspace and
owner** inside its query. A conversation belongs to the person who started it;
another member of the same workspace asking for it gets `None` -- and so a 404
-- exactly as an outsider would (ADR-0004).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from orbit.domain.access import AccessContext
from orbit.domain.conversations import ChatMessage, Conversation, FinishedAnswer, StartedTurn
from orbit.domain.models.pagination import Page


class ConversationRepository(Protocol):
    async def create(self, ctx: AccessContext, *, title: str) -> Conversation: ...

    async def get(self, ctx: AccessContext, conversation_id: uuid.UUID) -> Conversation | None:
        """The caller's own, undeleted conversation, or `None`."""
        ...

    async def list_page(self, ctx: AccessContext, *, limit: int, cursor: str | None) -> Page:
        """The caller's conversations in this workspace, newest first."""
        ...

    async def soft_delete(self, ctx: AccessContext, conversation_id: uuid.UUID) -> bool:
        """`False` if there was nothing of the caller's to delete."""
        ...

    async def start_turn(  # noqa: PLR0913 -- keyword-only turn metadata
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
        """Append the question and a PENDING answer, atomically.

        Serialised per conversation by a row lock, so two questions cannot
        claim the same ordinals. A PENDING answer created before
        `stale_before` belonged to a request that died; it is closed as
        `FAILED`/`ABANDONED` here rather than blocking the thread forever.
        A younger one means an answer is genuinely in progress:
        `AnswerInProgressError`.

        Raises `NotFoundError` if the conversation is not the caller's.
        """
        ...

    async def finish_answer(
        self, ctx: AccessContext, message_id: uuid.UUID, answer: FinishedAnswer
    ) -> bool:
        """Move a PENDING answer to its terminal state and write its citations.

        Conditional on the message still being PENDING; returns `False` if it
        was not (already finished, or closed as abandoned), and writes nothing.
        """
        ...

    async def recent_exchanges(
        self, ctx: AccessContext, conversation_id: uuid.UUID, *, before_ordinal: int, limit: int
    ) -> Sequence[ChatMessage]:
        """Up to `limit` most recent messages before `before_ordinal` that are
        worth showing the model as history -- questions, and answers that
        produced text -- oldest first. Citations are not loaded."""
        ...

    async def list_messages(
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        *,
        after_ordinal: int | None,
        limit: int,
    ) -> Sequence[ChatMessage]:
        """Messages in thread order, with their citations."""
        ...
