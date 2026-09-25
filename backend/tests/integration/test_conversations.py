"""Conversations, answers, and citations against real PostgreSQL.

The unit suite proves the answering rules on fakes. This proves what only the
database decides: owner scoping inside the SQL, the row lock and the unique
partial index behind "one answer in flight", the conditional PENDING ->
terminal transition, the composite foreign key that keeps a citation inside
its tenant, and the citation snapshot outliving the chunk it came from.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text

from orbit.application.answering.answer_question import AnswerPolicy, AnswerQuestion
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.core.clock import SystemClock
from orbit.domain.access import AccessContext, Role, SystemContext
from orbit.domain.conversations import (
    AnswerMetrics,
    Citation,
    FinishedAnswer,
    Grounding,
    MessageStatus,
    StopReason,
)
from orbit.domain.errors import AnswerInProgressError, ConflictError, NotFoundError
from orbit.domain.models.entities import Document, Membership, ProcessingOutcome, ProcessingStatus
from orbit.domain.processing.content import ChunkDraft, EmbeddedChunk
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider
from orbit.infrastructure.ai.reranking import PassthroughReranker
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter
from orbit.infrastructure.db.models import Chunk as ChunkRow
from orbit.infrastructure.db.models import MessageCitation as CitationRow
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from tests.integration.conftest import version_content

pytestmark = pytest.mark.integration

SYSTEM = SystemContext(reason="integration-test")
EMBEDDINGS = FakeEmbeddingProvider(dimensions=1536)
LONG_AGO = datetime.now(UTC) - timedelta(days=1)


class _SameUnitOfWork:
    """Every `async with uow_factory()` in the use case joins the test's
    transaction, which the fixture rolls back afterwards."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def __call__(self) -> _SameUnitOfWork:
        return self

    async def __aenter__(self) -> UnitOfWork:
        return self._uow

    async def __aexit__(self, *_: object) -> None:
        return None


async def _document(
    uow: UnitOfWork, ctx: AccessContext, title: str, passages: Sequence[str]
) -> Document:
    document = await uow.documents.create(
        ctx, title=title, folder_id=None, content=version_content(f"{title}-{uuid.uuid4()}")
    )
    version = document.current_version
    assert version is not None
    chunks, offset = [], 0
    for ordinal, passage in enumerate(passages):
        draft = ChunkDraft(
            ordinal=ordinal,
            text=passage,
            token_count=max(1, len(passage.split())),
            char_start=offset,
            char_end=offset + len(passage),
            page_start=ordinal + 1,
            page_end=ordinal + 1,
            heading_path=(title, "Policy"),
        )
        offset += len(passage) + 2
        chunks.append(
            EmbeddedChunk(
                draft=draft,
                embedding=EMBEDDINGS.vector(draft.embedding_input),
                embedded_at=datetime.now(UTC),
            )
        )
    await uow.processing.replace_chunks(
        SYSTEM, version, chunks, space=EMBEDDINGS.space, chunker_version="test"
    )
    await uow.processing.transition_version(
        SYSTEM,
        version.id,
        expected=frozenset({ProcessingStatus.PENDING}),
        outcome=ProcessingOutcome.ready(chunk_count=len(chunks)),
    )
    return document


def _answer_question(uow: UnitOfWork, llm: FakeLLMProvider | None = None) -> AnswerQuestion:
    factory = _SameUnitOfWork(uow)
    return AnswerQuestion(
        factory,
        search=HybridSearch(factory, EMBEDDINGS, SearchPolicy(ef_search=40)),
        reranker=PassthroughReranker(),
        llm=llm or FakeLLMProvider(),
        token_counter=ApproximateTokenCounter(),
        classifier=PipelineFailureClassifier(),
        policy=AnswerPolicy(),
        clock=SystemClock(),
    )


def _finished(citations: tuple[Citation, ...] = ()) -> FinishedAnswer:
    return FinishedAnswer(
        status=MessageStatus.COMPLETE,
        content="Answer [S1].",
        stop_reason=StopReason.COMPLETED,
        grounding=Grounding.GROUNDED if citations else Grounding.UNCITED,
        failure_stage=None,
        failure_code=None,
        model_id="m",
        prompt_tokens=10,
        completion_tokens=2,
        discarded_citation_count=0,
        metrics=AnswerMetrics(retrieval_ms=5, generation_ms=7, total_ms=12),
        citations=citations,
    )


class TestGroundedAnswerPersistence:
    async def test_an_answer_and_its_citations_round_trip_through_postgres(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        leave = await _document(
            uow,
            ctx,
            "Leave",
            [
                "Parental leave is sixteen weeks at full pay.",
                "Leave is booked through the HR portal.",
            ],
        )
        await _document(uow, ctx, "Expenses", ["Expense claims need an itemised receipt."])
        conversation = await uow.conversations.create(ctx, title="Leave")
        answer = _answer_question(uow, FakeLLMProvider(fabricate_citations=True))

        message = await answer.execute(ctx, conversation.id, "How long is parental leave?")

        assert message.grounding is Grounding.GROUNDED
        assert message.discarded_citation_count == 3
        (question, stored) = await uow.conversations.list_messages(
            ctx, conversation.id, after_ordinal=None, limit=10
        )
        assert question.content == "How long is parental leave?"
        assert stored.status is MessageStatus.COMPLETE
        assert stored.content == message.content
        assert stored.citations == message.citations
        assert stored.metrics is not None and stored.metrics.total_ms is not None
        chunk_ids = set(
            (
                await uow.session.scalars(
                    select(ChunkRow.id).where(ChunkRow.document_id == leave.id)
                )
            ).all()
        )
        for citation in stored.citations:
            assert citation.document_id == leave.id
            assert citation.chunk_id in chunk_ids
            assert citation.version_number == 1
            assert citation.heading_path == "Leave > Policy"
            assert citation.page_from is not None

    async def test_a_citation_outlives_the_chunk_it_snapshots(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        leave = await _document(uow, ctx, "Leave", ["Parental leave is sixteen weeks."])
        conversation = await uow.conversations.create(ctx, title="Leave")
        message = await _answer_question(uow).execute(ctx, conversation.id, "parental leave")
        assert message.citations

        # Reprocessing rebuilds chunks: the live link goes, the snapshot stays.
        await uow.session.execute(delete(ChunkRow).where(ChunkRow.document_id == leave.id))
        await uow.session.flush()

        (_, stored) = await uow.conversations.list_messages(
            ctx, conversation.id, after_ordinal=None, limit=10
        )
        (citation,) = stored.citations
        assert citation.chunk_id is None
        assert citation.snippet == "Parental leave is sixteen weeks."
        assert citation.document_id == leave.id
        assert citation.chunk_ordinal == 0


class TestOwnership:
    async def test_another_member_cannot_see_or_use_my_conversation(
        self, uow: UnitOfWork, ctx: AccessContext, membership: Membership
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="Mine")
        colleague = AccessContext(
            user_id=membership.user_id, workspace_id=ctx.workspace_id, role=Role.MEMBER
        )

        assert await uow.conversations.get(colleague, conversation.id) is None
        assert (await uow.conversations.list_page(colleague, limit=10, cursor=None)).items == ()
        assert not await uow.conversations.soft_delete(colleague, conversation.id)
        with pytest.raises(NotFoundError):
            await uow.conversations.start_turn(
                colleague,
                conversation.id,
                question="q",
                model_id="m",
                prompt_version="p",
                request_id=None,
                stale_before=LONG_AGO,
            )
        assert (
            await uow.conversations.list_messages(
                colleague, conversation.id, after_ordinal=None, limit=10
            )
            == []
        )

    async def test_a_citation_cannot_point_into_another_workspace(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        foreign = await _document(uow, other_ctx, "Secret", ["Globex revenue was forty million."])
        conversation = await uow.conversations.create(ctx, title="Mine")
        turn = await uow.conversations.start_turn(
            ctx,
            conversation.id,
            question="q",
            model_id="m",
            prompt_version="p",
            request_id=None,
            stale_before=LONG_AGO,
        )
        forged = Citation(
            handle="S1",
            ordinal=0,
            document_id=foreign.id,
            document_title="Secret",
            document_version_id=None,
            version_number=None,
            chunk_id=None,
            chunk_ordinal=None,
            page_from=None,
            page_to=None,
            heading_path=None,
            char_start=None,
            char_end=None,
            snippet="Globex revenue was forty million.",
        )

        with pytest.raises(ConflictError):
            await uow.conversations.finish_answer(ctx, turn.answer.id, _finished((forged,)))


class TestTurnLifecycle:
    async def _turn(
        self, uow: UnitOfWork, ctx: AccessContext, conversation_id: uuid.UUID
    ) -> uuid.UUID:
        turn = await uow.conversations.start_turn(
            ctx,
            conversation_id,
            question="q",
            model_id="m",
            prompt_version="p",
            request_id="req-1",
            stale_before=LONG_AGO,
        )
        assert (turn.question.ordinal + 1, turn.answer.status) == (
            turn.answer.ordinal,
            MessageStatus.PENDING,
        )
        return turn.answer.id

    async def test_one_answer_in_flight_per_conversation(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        await self._turn(uow, ctx, conversation.id)

        with pytest.raises(AnswerInProgressError):
            await self._turn(uow, ctx, conversation.id)

    async def test_the_database_itself_refuses_a_second_pending_answer(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        await self._turn(uow, ctx, conversation.id)

        with pytest.raises(Exception, match="uq_messages_one_pending_per_conversation"):
            await uow.session.execute(
                text(
                    "INSERT INTO messages (id, workspace_id, conversation_id, role, ordinal, "
                    "content, status) VALUES (:id, :ws, :c, 'assistant', 99, '', 'pending')"
                ),
                {"id": uuid.uuid4(), "ws": ctx.workspace_id, "c": conversation.id},
            )

    async def test_an_answer_is_finished_exactly_once(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        answer_id = await self._turn(uow, ctx, conversation.id)

        assert await uow.conversations.finish_answer(ctx, answer_id, _finished())
        assert not await uow.conversations.finish_answer(
            ctx, answer_id, dataclasses.replace(_finished(), content="rewritten")
        )
        (_, stored) = await uow.conversations.list_messages(
            ctx, conversation.id, after_ordinal=None, limit=10
        )
        assert stored.content == "Answer [S1]."

    async def test_a_stale_pending_answer_is_closed_as_abandoned(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        abandoned = await self._turn(uow, ctx, conversation.id)

        await uow.conversations.start_turn(
            ctx,
            conversation.id,
            question="again",
            model_id="m",
            prompt_version="p",
            request_id=None,
            stale_before=datetime.now(UTC) + timedelta(minutes=1),
        )

        messages = await uow.conversations.list_messages(
            ctx, conversation.id, after_ordinal=None, limit=10
        )
        closed = next(m for m in messages if m.id == abandoned)
        assert (closed.status, closed.stop_reason, closed.failure_code) == (
            MessageStatus.FAILED,
            StopReason.ABANDONED,
            "ANSWER_ABANDONED",
        )
        assert [m.ordinal for m in messages] == [0, 1, 2, 3]

    async def test_a_failed_answer_must_say_why(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        answer_id = await self._turn(uow, ctx, conversation.id)

        with pytest.raises(Exception, match="failed_messages_say_why"):
            await uow.session.execute(
                text("UPDATE messages SET status = 'failed' WHERE id = :id"), {"id": answer_id}
            )

    async def test_history_excludes_failed_and_pending_answers(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        conversation = await uow.conversations.create(ctx, title="c")
        answered = await self._turn(uow, ctx, conversation.id)
        await uow.conversations.finish_answer(ctx, answered, _finished())
        await self._turn(uow, ctx, conversation.id)  # still pending

        history = await uow.conversations.recent_exchanges(
            ctx, conversation.id, before_ordinal=10, limit=10
        )

        assert [(m.role.value, m.ordinal) for m in history] == [
            ("user", 0),
            ("assistant", 1),
            ("user", 2),
        ]

    async def test_deleting_citations_with_their_message(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        await _document(uow, ctx, "Leave", ["Parental leave is sixteen weeks."])
        conversation = await uow.conversations.create(ctx, title="Leave")
        await _answer_question(uow).execute(ctx, conversation.id, "parental leave")
        assert await uow.session.scalar(select(CitationRow.id).limit(1)) is not None

        await uow.session.execute(
            text("DELETE FROM conversations WHERE id = :id"), {"id": conversation.id}
        )

        assert (
            await uow.session.scalar(
                select(CitationRow.id).where(CitationRow.workspace_id == ctx.workspace_id)
            )
            is None
        )
