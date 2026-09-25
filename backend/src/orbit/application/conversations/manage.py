"""Conversation lifecycle: create, list, read, delete.

Asking a question is `orbit.application.answering.answer_question`. Every use
case here requires `chat:use`, and every repository call is scoped to the
caller as owner, so another member's conversation is a 404 -- never a 403,
which would confirm it exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from orbit.domain.access import AccessContext, Permission
from orbit.domain.conversations import ChatMessage, Conversation, normalize_title
from orbit.domain.errors import NotFoundError
from orbit.domain.models.pagination import Page, clamp_limit
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


def _not_found(conversation_id: uuid.UUID) -> NotFoundError:
    return NotFoundError("Conversation not found.", conversation_id=str(conversation_id))


class CreateConversation:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, *, title: str | None) -> Conversation:
        ctx.require(Permission.CHAT_USE)
        normalized = normalize_title(title)
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.create(ctx, title=normalized)
            await uow.commit()
        return conversation


class ListConversations:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, *, limit: int | None, cursor: str | None) -> Page:
        ctx.require(Permission.CHAT_USE)
        async with self._uow_factory() as uow:
            return await uow.conversations.list_page(ctx, limit=clamp_limit(limit), cursor=cursor)


class GetConversation:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, conversation_id: uuid.UUID) -> Conversation:
        ctx.require(Permission.CHAT_USE)
        async with self._uow_factory() as uow:
            conversation = await uow.conversations.get(ctx, conversation_id)
        if conversation is None:
            raise _not_found(conversation_id)
        return conversation


class ListMessages:
    """A conversation's messages in thread order, with citations."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        *,
        after_ordinal: int | None,
        limit: int | None,
    ) -> Sequence[ChatMessage]:
        ctx.require(Permission.CHAT_USE)
        async with self._uow_factory() as uow:
            # Checked first so an empty result means "no messages", never
            # "not yours" -- and the latter is a 404.
            if await uow.conversations.get(ctx, conversation_id) is None:
                raise _not_found(conversation_id)
            return await uow.conversations.list_messages(
                ctx,
                conversation_id,
                after_ordinal=after_ordinal,
                limit=clamp_limit(limit),
            )


class DeleteConversation:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, conversation_id: uuid.UUID) -> None:
        ctx.require(Permission.CHAT_USE)
        async with self._uow_factory() as uow:
            deleted = await uow.conversations.soft_delete(ctx, conversation_id)
            await uow.commit()
        if not deleted:
            raise _not_found(conversation_id)
