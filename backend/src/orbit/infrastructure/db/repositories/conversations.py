"""Conversation repository.

Every query is scoped by workspace **and owner**, inside the SQL. There is no
method that reads a conversation or a message by id alone (ADR-0004).

Turn ordering is serialised by locking the conversation row, and "one answer
in flight" is additionally a unique partial index
(`uq_messages_one_pending_per_conversation`), so a race the lock somehow
missed fails as a conflict instead of interleaving two answers.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext
from orbit.domain.conversations import (
    AnswerMetrics,
    ChatMessage,
    Citation,
    Conversation,
    FailureStage,
    FinishedAnswer,
    Grounding,
    MessageRole,
    MessageStatus,
    StartedTurn,
    StopReason,
)
from orbit.domain.errors import AnswerInProgressError, NotFoundError
from orbit.domain.models.pagination import Cursor, Page
from orbit.infrastructure.db.errors import flush_translating_conflicts, translate_integrity_error
from orbit.infrastructure.db.models import AnswerGrounding
from orbit.infrastructure.db.models import Conversation as ConversationRow
from orbit.infrastructure.db.models import Message as MessageRow
from orbit.infrastructure.db.models import MessageCitation as CitationRow
from orbit.infrastructure.db.models import MessageRole as RoleColumn
from orbit.infrastructure.db.models import MessageStatus as StatusColumn

_TURN_CONSTRAINTS = (
    "uq_messages_one_pending_per_conversation",
    "uq_messages_conversation_id_ordinal",
)


def _owned(ctx: AccessContext) -> Select[tuple[uuid.UUID]]:
    """Ids of the caller's live conversations in this workspace."""
    return select(ConversationRow.id).where(
        ConversationRow.workspace_id == ctx.workspace_id,
        ConversationRow.user_id == ctx.user_id,
        ConversationRow.deleted_at.is_(None),
    )


def _conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _citation(row: CitationRow) -> Citation:
    return Citation(
        handle=row.handle,
        ordinal=row.ordinal,
        document_id=row.document_id,
        document_title=row.document_title,
        document_version_id=row.document_version_id,
        version_number=row.version_number,
        chunk_id=row.chunk_id,
        chunk_ordinal=row.chunk_ordinal,
        page_from=row.page_from,
        page_to=row.page_to,
        heading_path=row.heading_path,
        char_start=row.char_start,
        char_end=row.char_end,
        snippet=row.snippet,
    )


def _message(row: MessageRow, citations: Sequence[Citation] = ()) -> ChatMessage:
    is_answer = row.role is RoleColumn.ASSISTANT
    return ChatMessage(
        id=row.id,
        conversation_id=row.conversation_id,
        workspace_id=row.workspace_id,
        role=MessageRole(row.role.value),
        ordinal=row.ordinal,
        content=row.content,
        status=MessageStatus(row.status.value),
        created_at=row.created_at,
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        grounding=Grounding(row.grounding.value) if row.grounding else None,
        stop_reason=StopReason(row.stop_reason) if row.stop_reason else None,
        failure_stage=FailureStage(row.failure_stage) if row.failure_stage else None,
        failure_code=row.failure_code,
        discarded_citation_count=row.discarded_citation_count,
        metrics=AnswerMetrics(
            retrieval_ms=row.retrieval_ms,
            generation_ms=row.generation_ms,
            total_ms=row.total_ms,
            first_token_ms=row.first_token_ms,
            retrieved_count=row.retrieved_count,
            context_tokens=row.context_tokens,
            retrieval_degraded=row.retrieval_degraded,
        )
        if is_answer
        else None,
        citations=tuple(citations),
    )


class SqlConversationRepository:
    def __init__(self, session: AsyncSession, *, cursor_secret: str) -> None:
        self._session = session
        self._cursor_secret = cursor_secret

    async def create(self, ctx: AccessContext, *, title: str) -> Conversation:
        row = ConversationRow(
            id=new_uuid7(), workspace_id=ctx.workspace_id, user_id=ctx.user_id, title=title
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return _conversation(row)

    async def get(self, ctx: AccessContext, conversation_id: uuid.UUID) -> Conversation | None:
        row = await self._session.scalar(
            select(ConversationRow).where(
                ConversationRow.id == conversation_id,
                ConversationRow.workspace_id == ctx.workspace_id,
                ConversationRow.user_id == ctx.user_id,
                ConversationRow.deleted_at.is_(None),
            )
        )
        return _conversation(row) if row else None

    async def list_page(self, ctx: AccessContext, *, limit: int, cursor: str | None) -> Page:
        """Newest first, keyset-paginated along
        `ix_conversations_workspace_id_user_id_created_at`."""
        stmt = select(ConversationRow).where(
            ConversationRow.workspace_id == ctx.workspace_id,
            ConversationRow.user_id == ctx.user_id,
            ConversationRow.deleted_at.is_(None),
        )
        if cursor is not None:
            position = Cursor.decode(cursor, self._cursor_secret)
            stmt = stmt.where(
                tuple_(ConversationRow.created_at, ConversationRow.id)
                < (position.created_at, position.row_id)
            )
        stmt = stmt.order_by(ConversationRow.created_at.desc(), ConversationRow.id.desc()).limit(
            limit + 1
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        items = tuple(_conversation(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit and items:
            last = items[-1]
            next_cursor = Cursor(created_at=last.created_at, row_id=last.id).encode(
                self._cursor_secret
            )
        return Page(items=items, next_cursor=next_cursor)

    async def soft_delete(self, ctx: AccessContext, conversation_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            update(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.workspace_id == ctx.workspace_id,
                ConversationRow.user_id == ctx.user_id,
                ConversationRow.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), version=ConversationRow.version + 1)
            .returning(ConversationRow.id)
        )
        return result.scalar_one_or_none() is not None

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
        # The row lock serialises turns in this conversation: ordinals are
        # computed and claimed while it is held, and released at commit.
        conversation = await self._session.scalar(
            select(ConversationRow)
            .where(
                ConversationRow.id == conversation_id,
                ConversationRow.workspace_id == ctx.workspace_id,
                ConversationRow.user_id == ctx.user_id,
                ConversationRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if conversation is None:
            msg = "Conversation not found."
            raise NotFoundError(msg, conversation_id=str(conversation_id))

        pending = await self._session.scalar(
            select(MessageRow).where(
                MessageRow.conversation_id == conversation_id,
                MessageRow.status == StatusColumn.PENDING,
            )
        )
        if pending is not None:
            if pending.created_at >= stale_before:
                msg = "The previous question in this conversation is still being answered."
                raise AnswerInProgressError(msg, conversation_id=str(conversation_id))
            await self._session.execute(
                update(MessageRow)
                .where(MessageRow.id == pending.id, MessageRow.status == StatusColumn.PENDING)
                .values(
                    status=StatusColumn.FAILED,
                    stop_reason=StopReason.ABANDONED.value,
                    failure_code="ANSWER_ABANDONED",
                    updated_at=func.now(),
                )
            )

        highest = await self._session.scalar(
            select(func.max(MessageRow.ordinal)).where(
                MessageRow.conversation_id == conversation_id
            )
        )
        ordinal = 0 if highest is None else highest + 1
        question_row = MessageRow(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            conversation_id=conversation_id,
            role=RoleColumn.USER,
            ordinal=ordinal,
            content=question,
            status=StatusColumn.COMPLETE,
        )
        answer_row = MessageRow(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            conversation_id=conversation_id,
            role=RoleColumn.ASSISTANT,
            ordinal=ordinal + 1,
            content="",
            status=StatusColumn.PENDING,
            model_id=model_id,
            prompt_version=prompt_version,
            request_id=request_id,
        )
        self._session.add_all([question_row, answer_row])
        try:
            # Deliberately the raw flush: the handler below distinguishes one
            # constraint from the rest, which needs the driver's own error.
            await self._session.flush()
        except IntegrityError as exc:
            # The lock makes this unreachable between well-behaved callers;
            # if another writer slipped past it, the constraints still hold
            # and the loser learns why.
            if any(name in str(exc.orig) for name in _TURN_CONSTRAINTS):
                msg = "The previous question in this conversation is still being answered."
                raise AnswerInProgressError(msg, conversation_id=str(conversation_id)) from exc
            raise translate_integrity_error(exc) from exc
        await self._session.refresh(question_row)
        await self._session.refresh(answer_row)
        return StartedTurn(question=_message(question_row), answer=_message(answer_row))

    async def finish_answer(
        self, ctx: AccessContext, message_id: uuid.UUID, answer: FinishedAnswer
    ) -> bool:
        metrics = answer.metrics
        finished = await self._session.scalar(
            update(MessageRow)
            .where(
                MessageRow.id == message_id,
                MessageRow.workspace_id == ctx.workspace_id,
                MessageRow.conversation_id.in_(_owned(ctx)),
                MessageRow.role == RoleColumn.ASSISTANT,
                MessageRow.status == StatusColumn.PENDING,
            )
            .values(
                status=StatusColumn(answer.status.value),
                content=answer.content,
                stop_reason=answer.stop_reason.value,
                grounding=AnswerGrounding(answer.grounding.value) if answer.grounding else None,
                failure_stage=answer.failure_stage.value if answer.failure_stage else None,
                failure_code=answer.failure_code,
                model_id=answer.model_id,
                prompt_tokens=answer.prompt_tokens,
                completion_tokens=answer.completion_tokens,
                discarded_citation_count=answer.discarded_citation_count,
                retrieval_ms=metrics.retrieval_ms,
                generation_ms=metrics.generation_ms,
                total_ms=metrics.total_ms,
                first_token_ms=metrics.first_token_ms,
                retrieved_count=metrics.retrieved_count,
                context_tokens=metrics.context_tokens,
                retrieval_degraded=metrics.retrieval_degraded,
                updated_at=func.now(),
            )
            .returning(MessageRow.id)
        )
        if finished is None:
            return False
        self._session.add_all(
            CitationRow(
                id=new_uuid7(),
                workspace_id=ctx.workspace_id,
                message_id=message_id,
                handle=citation.handle,
                ordinal=citation.ordinal,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                document_version_id=citation.document_version_id,
                version_number=citation.version_number,
                chunk_ordinal=citation.chunk_ordinal,
                document_title=citation.document_title,
                heading_path=citation.heading_path,
                page_from=citation.page_from,
                page_to=citation.page_to,
                char_start=citation.char_start,
                char_end=citation.char_end,
                snippet=citation.snippet,
            )
            for citation in answer.citations
        )
        try:
            await flush_translating_conflicts(self._session)
        except IntegrityError as exc:
            # e.g. a citation naming a document outside this workspace: the
            # composite foreign key refuses it (ADR-0006, ADR-0004).
            raise translate_integrity_error(exc) from exc
        return True

    async def recent_exchanges(
        self, ctx: AccessContext, conversation_id: uuid.UUID, *, before_ordinal: int, limit: int
    ) -> Sequence[ChatMessage]:
        rows = (
            await self._session.scalars(
                select(MessageRow)
                .where(
                    MessageRow.conversation_id == conversation_id,
                    MessageRow.conversation_id.in_(_owned(ctx)),
                    MessageRow.workspace_id == ctx.workspace_id,
                    MessageRow.ordinal < before_ordinal,
                    or_(
                        MessageRow.role == RoleColumn.USER,
                        and_(
                            MessageRow.status.in_([StatusColumn.COMPLETE, StatusColumn.PARTIAL]),
                            MessageRow.content != "",
                        ),
                    ),
                )
                .order_by(MessageRow.ordinal.desc())
                .limit(limit)
            )
        ).all()
        return [_message(row) for row in reversed(rows)]

    async def list_messages(
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        *,
        after_ordinal: int | None,
        limit: int,
    ) -> Sequence[ChatMessage]:
        stmt = select(MessageRow).where(
            MessageRow.conversation_id == conversation_id,
            MessageRow.conversation_id.in_(_owned(ctx)),
            MessageRow.workspace_id == ctx.workspace_id,
        )
        if after_ordinal is not None:
            stmt = stmt.where(MessageRow.ordinal > after_ordinal)
        rows = (await self._session.scalars(stmt.order_by(MessageRow.ordinal).limit(limit))).all()
        if not rows:
            return []
        # One query for every message's citations, not one per message.
        citations: dict[uuid.UUID, list[Citation]] = defaultdict(list)
        for citation in await self._session.scalars(
            select(CitationRow)
            .where(
                CitationRow.workspace_id == ctx.workspace_id,
                CitationRow.message_id.in_([row.id for row in rows]),
            )
            .order_by(CitationRow.message_id, CitationRow.ordinal)
        ):
            citations[citation.message_id].append(_citation(citation))
        return [_message(row, citations.get(row.id, ())) for row in rows]
