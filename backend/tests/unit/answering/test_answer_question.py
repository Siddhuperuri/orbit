"""Grounded question answering, end to end on fakes.

Real upload, parsing, chunking, embedding, hybrid retrieval, context
construction, citation resolution, and persistence rules; in-memory storage
and a scriptable language model. No network and no API key -- the system is
exercised exactly as it runs under `ORBIT_AI_PROVIDER=fake`, plus the
adversarial behaviour a real provider exhibits on a bad day.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import pytest

from orbit.application.answering.answer_question import (
    AnswerCompleted,
    AnswerDelta,
    AnswerEvent,
    AnswerFailed,
    AnswerPolicy,
    AnswerQuestion,
    AnswerStarted,
    RetrievalCompleted,
)
from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.conversations.manage import CreateConversation, ListMessages
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.answering import INSUFFICIENT_EVIDENCE_MARKER, SOURCE_BLOCK
from orbit.domain.conversations import (
    ChatMessage,
    FailureStage,
    Grounding,
    MessageRole,
    MessageStatus,
    StopReason,
)
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import (
    AIProviderCircuitOpenError,
    AIProviderRateLimitedError,
    AIProviderResponseInvalidError,
    AIProviderUnavailableError,
    AnswerInProgressError,
    ConversationUnavailableError,
    DatabaseUnavailableError,
    DependencyUnavailableError,
    GenerationFailedError,
    GenerationRateLimitedError,
    GenerationTimeoutError,
    NotFoundError,
    RateLimitedError,
    RetrievalFailedError,
    ValidationError,
)
from orbit.domain.llm import FinishReason
from orbit.domain.models.entities import Document
from orbit.domain.retrieval import SearchQuery, SearchResponse
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider
from orbit.infrastructure.ai.reranking import PassthroughReranker
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from tests.conftest import build_settings
from tests.unit.fakes.scripted_llm import ScriptedLLM
from tests.unit.fakes.security_doubles import InMemoryRateLimiter, RecordingAuditSink
from tests.unit.processing.harness import Pipeline, build_pipeline

LEAVE = (
    "Parental leave is sixteen weeks at full pay. It can start up to four weeks "
    "before the expected birth and is booked through the HR portal. "
) * 3
EXPENSES = (
    "Expense claims need an itemised receipt. Claims above five hundred dollars "
    "need approval from a budget holder before reimbursement. "
) * 3
ERRORS = (
    "Error ERR-4012 means the access token expired; request a new token. "
    "Error ERR-5031 means an upstream service timed out; retry with backoff. "
) * 3
GLOBEX = "Globex quarterly revenue was forty million dollars, driven by turbine sales. " * 3

QUESTION = "How long is parental leave?"


@dataclass
class Harness:
    pipeline: Pipeline
    llm: ScriptedLLM
    answer: AnswerQuestion
    documents: dict[str, Document]
    conversation_id: uuid.UUID

    @property
    def ctx(self) -> AccessContext:
        return self.pipeline.ctx

    def stored(self, message_id: uuid.UUID) -> ChatMessage:
        return self.pipeline.uow_factory.state.messages[message_id]

    def thread(self) -> list[ChatMessage]:
        return sorted(
            (
                m
                for m in self.pipeline.uow_factory.state.messages.values()
                if m.conversation_id == self.conversation_id
            ),
            key=lambda m: m.ordinal,
        )

    def last_answer(self) -> ChatMessage:
        return self.thread()[-1]

    def chunk_ids(self, key: str) -> set[uuid.UUID]:
        return {c.chunk_id for c in self.pipeline.chunks(self.documents[key])}

    async def ask(self, question: str = QUESTION, **options: object) -> ChatMessage:
        return await self.answer.execute(self.ctx, self.conversation_id, question, **options)  # type: ignore[arg-type]

    async def stream(self, question: str = QUESTION) -> list[AnswerEvent]:
        prepared = await self.answer.prepare(self.ctx, self.conversation_id, question)
        return [event async for event in self.answer.events(prepared, stream=True)]


class _FailingSearch(HybridSearch):
    """Retrieval whose dependency is down."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def execute(
        self,
        ctx: AccessContext,
        query: SearchQuery,
        *,
        client_ip: str | None = None,
        metered: bool = True,
    ) -> SearchResponse:
        self.calls += 1
        self.metered = metered
        raise self._error


@dataclass
class _EmbeddingOutage:
    """The query embedder is down; everything else is the pipeline's own."""

    inner: object

    @property
    def space(self) -> EmbeddingSpace:
        return self.inner.space  # type: ignore[attr-defined, no-any-return]

    @property
    def max_batch_size(self) -> int:
        return 16

    @property
    def max_batch_tokens(self) -> int:
        return 10_000

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise AssertionError

    async def embed_query(self, text: str) -> Sequence[float]:
        msg = "down"
        raise AIProviderUnavailableError(msg)


async def _harness(  # noqa: PLR0913
    *,
    llm: ScriptedLLM | None = None,
    policy: AnswerPolicy | None = None,
    corpus: bool = True,
    search: HybridSearch | None = None,
    rate_limit: AuthRateLimitGuard | None = None,
    degraded_embeddings: bool = False,
) -> Harness:
    pipeline = await build_pipeline()
    documents: dict[str, Document] = {}
    if corpus:
        for key, text in (("leave", LEAVE), ("expenses", EXPENSES), ("errors", ERRORS)):
            documents[key] = await pipeline.upload(f"{key}.txt", text.encode())
        await pipeline.drain()
    llm = llm or ScriptedLLM()
    embedder = _EmbeddingOutage(pipeline.embedder) if degraded_embeddings else pipeline.embedder
    answer = AnswerQuestion(
        pipeline.uow_factory,
        search=search or HybridSearch(pipeline.uow_factory, embedder, SearchPolicy(ef_search=40)),
        reranker=PassthroughReranker(),
        llm=llm,
        token_counter=ApproximateTokenCounter(),
        classifier=PipelineFailureClassifier(),
        policy=policy or AnswerPolicy(),
        clock=pipeline.clock,
        rate_limit=rate_limit,
    )
    conversation = await CreateConversation(pipeline.uow_factory).execute(pipeline.ctx, title=None)
    return Harness(pipeline, llm, answer, documents, conversation.id)


def _sources_in_prompt(llm: ScriptedLLM, call: int = 0) -> list[tuple[str, str]]:
    return SOURCE_BLOCK.findall(llm.prompts[call][-1].content)


# ---------------------------------------------------------------------------
# Grounded answers and citation correctness
# ---------------------------------------------------------------------------


class TestGroundedAnswer:
    async def test_the_answer_cites_the_chunks_it_was_given(self) -> None:
        h = await _harness()

        message = await h.ask()

        assert message.status is MessageStatus.COMPLETE
        assert message.grounding is Grounding.GROUNDED
        assert message.stop_reason is StopReason.COMPLETED
        assert message.citations
        assert "sixteen weeks" in message.content
        for citation in message.citations:
            assert f"[{citation.handle}]" in message.content
            assert citation.document_id == h.documents["leave"].id

    async def test_every_citation_resolves_to_its_document_version_chunk_and_location(
        self,
    ) -> None:
        h = await _harness()

        message = await h.ask()

        version = h.pipeline.version(h.documents["leave"])
        stored = {c.chunk_id: c for c in h.pipeline.chunks(h.documents["leave"])}
        for citation in message.citations:
            chunk = stored[citation.chunk_id]  # type: ignore[index]
            assert citation.document_title == "leave.txt"
            assert citation.document_version_id == version.id
            assert citation.version_number == version.version_number
            assert citation.chunk_ordinal == chunk.ordinal
            assert (citation.char_start, citation.char_end) == (chunk.char_start, chunk.char_end)
            assert (citation.page_from, citation.page_to) == (chunk.page_start, chunk.page_end)
            assert citation.heading_path == chunk.heading_path
            assert citation.snippet == " ".join(chunk.text.split())

    async def test_the_answer_and_its_citations_are_persisted_with_the_question(self) -> None:
        h = await _harness()

        message = await h.ask()

        question, answer = h.thread()
        assert (question.role, question.ordinal, question.content) == (
            MessageRole.USER,
            0,
            QUESTION,
        )
        assert (answer.role, answer.ordinal) == (MessageRole.ASSISTANT, 1)
        assert answer == message
        listed = await ListMessages(h.pipeline.uow_factory).execute(
            h.ctx, h.conversation_id, after_ordinal=None, limit=None
        )
        assert [m.id for m in listed] == [question.id, answer.id]
        assert listed[1].citations == message.citations

    async def test_only_retrieved_text_within_the_budget_reaches_the_model(self) -> None:
        h = await _harness(policy=AnswerPolicy(max_context_tokens=500))
        handbook_text = "\n\n".join(
            f"Section {n}. Parental leave rule {n}: employees in region {n} receive "
            f"{n} additional days of parental leave, approved by the regional office {n}."
            for n in range(1, 400)
        )
        handbook = await h.pipeline.upload("handbook.txt", handbook_text.encode())
        await h.pipeline.drain()
        assert len(h.pipeline.chunks(handbook)) > 5

        message = await h.ask()

        workspace_text = {
            c.text for d in [*h.documents.values(), handbook] for c in h.pipeline.chunks(d)
        }
        sources = _sources_in_prompt(h.llm)
        assert sources
        assert all(body in workspace_text for _, body in sources)
        assert message.metrics is not None and message.metrics.context_tokens is not None
        assert message.metrics.context_tokens <= 500
        # The document is never sent whole: a bounded handful of its chunks at most.
        sent = sum(len(body) for _, body in sources)
        assert sent < len(handbook_text) / 5

    async def test_latency_and_token_usage_are_measured(self) -> None:
        h = await _harness()

        message = await h.ask()

        metrics = message.metrics
        assert metrics is not None
        assert metrics.retrieval_ms is not None and metrics.retrieval_ms >= 0
        assert metrics.generation_ms is not None and metrics.generation_ms >= 0
        assert metrics.first_token_ms is not None
        assert metrics.total_ms is not None
        assert metrics.total_ms >= metrics.retrieval_ms
        assert metrics.retrieved_count
        assert (message.prompt_tokens, message.completion_tokens) == (120, 30)
        assert message.model_id == "scripted-llm"
        assert message.prompt_version

    async def test_the_default_fake_provider_answers_the_whole_path_offline(self) -> None:
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider()))

        message = await h.ask("How many weeks of parental leave?")

        assert message.grounding is Grounding.GROUNDED
        assert message.prompt_tokens is not None


class TestHallucinatedCitations:
    async def test_fabricated_handles_never_reach_the_answer_or_its_citations(self) -> None:
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider(fabricate_citations=True)))

        message = await h.ask()

        offered = {handle for handle, _ in _sources_in_prompt(h.llm)}
        assert {c.handle for c in message.citations} <= offered
        assert message.discarded_citation_count == 3
        for fabricated in ("S99", "S42", "s77", "S77"):
            assert fabricated not in message.content
        assert h.stored(message.id).discarded_citation_count == 3

    async def test_every_handle_outside_the_context_is_discarded(self) -> None:
        text = " ".join(f"Claim {n} [S{n}]." for n in range(1, 21))
        h = await _harness(llm=ScriptedLLM(text=text))

        message = await h.ask()

        offered = len(_sources_in_prompt(h.llm))
        assert len(message.citations) == offered
        assert message.discarded_citation_count == 20 - offered
        cited = {c.chunk_id for c in message.citations}
        all_chunks = set().union(*(h.chunk_ids(k) for k in h.documents))
        assert cited <= all_chunks

    async def test_a_source_named_by_title_is_not_a_citation(self) -> None:
        h = await _harness(
            llm=ScriptedLLM(text="Leave is sixteen weeks (see leave.txt, page 3, doc 1234).")
        )

        message = await h.ask()

        assert message.citations == ()
        assert message.grounding is Grounding.UNCITED


# ---------------------------------------------------------------------------
# No evidence, insufficient evidence
# ---------------------------------------------------------------------------


class TestWithoutEvidence:
    async def test_empty_retrieval_answers_without_asking_the_model(self) -> None:
        h = await _harness(corpus=False)

        message = await h.ask()

        assert h.llm.calls == 0
        assert message.status is MessageStatus.COMPLETE
        assert message.grounding is Grounding.NO_EVIDENCE
        assert message.citations == ()
        assert "couldn't find anything" in message.content
        assert message.model_id is None
        assert message.metrics is not None and message.metrics.retrieved_count == 0

    async def test_insufficient_evidence_is_declared_and_the_marker_stripped(self) -> None:
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider()))

        message = await h.ask("Which airline do we fly with?")

        assert h.llm.calls == 1
        assert message.grounding is Grounding.INSUFFICIENT_EVIDENCE
        assert message.citations == ()
        assert INSUFFICIENT_EVIDENCE_MARKER not in message.content
        assert message.content

    async def test_a_bare_marker_becomes_a_readable_answer(self) -> None:
        h = await _harness(llm=ScriptedLLM(text=INSUFFICIENT_EVIDENCE_MARKER))

        message = await h.ask()

        assert message.status is MessageStatus.COMPLETE
        assert message.grounding is Grounding.INSUFFICIENT_EVIDENCE
        assert "don't contain enough information" in message.content

    async def test_the_marker_is_never_streamed(self) -> None:
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider(), chunk_chars=3))

        events = await h.stream("Which airline do we fly with?")

        streamed = "".join(e.text for e in events if isinstance(e, AnswerDelta))
        assert INSUFFICIENT_EVIDENCE_MARKER not in streamed
        assert "INSUFFICIENT" not in streamed
        assert streamed.startswith("The provided sources")

    async def test_a_context_budget_too_small_for_any_source_does_not_call_the_model(
        self,
    ) -> None:
        h = await _harness(policy=AnswerPolicy(max_context_tokens=5))

        message = await h.ask()

        assert h.llm.calls == 0
        assert message.grounding is Grounding.INSUFFICIENT_EVIDENCE
        assert message.metrics is not None and message.metrics.retrieved_count


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    async def _with_other_tenant(self) -> tuple[Harness, Document]:
        h = await _harness()
        workspace = await CreateWorkspace(h.pipeline.uow_factory).execute(
            name="Globex", created_by_user_id=h.ctx.user_id
        )
        other = AccessContext(user_id=h.ctx.user_id, workspace_id=workspace.id, role=Role.OWNER)
        foreign = await h.pipeline.upload("globex.txt", GLOBEX.encode(), ctx=other)
        await h.pipeline.drain()
        return h, foreign

    async def test_another_workspaces_documents_never_reach_the_prompt_or_citations(
        self,
    ) -> None:
        h, foreign = await self._with_other_tenant()

        message = await h.ask("What was Globex quarterly revenue from turbine sales?")

        prompt = "\n".join(m.content for m in h.llm.prompts[0])
        assert "forty million" not in prompt
        assert all(c.document_id != foreign.id for c in message.citations)

    async def test_filtering_to_a_foreign_document_finds_nothing(self) -> None:
        h, foreign = await self._with_other_tenant()

        message = await h.ask("What was Globex quarterly revenue?", document_ids=[foreign.id])

        assert h.llm.calls == 0
        assert message.grounding is Grounding.NO_EVIDENCE
        assert "the selected documents" in message.content

    async def test_another_members_conversation_is_not_found(self) -> None:
        h = await _harness()
        colleague = AccessContext(
            user_id=uuid.uuid4(), workspace_id=h.ctx.workspace_id, role=Role.MEMBER
        )

        with pytest.raises(NotFoundError):
            await h.answer.execute(colleague, h.conversation_id, QUESTION)
        assert h.llm.calls == 0
        assert h.thread() == []

    async def test_an_unknown_conversation_is_not_found(self) -> None:
        h = await _harness()
        with pytest.raises(NotFoundError):
            await h.answer.execute(h.ctx, uuid.uuid4(), QUESTION)


# ---------------------------------------------------------------------------
# Generation failures
# ---------------------------------------------------------------------------


class TestGenerationFailures:
    async def test_provider_outage_is_a_generation_failure_and_is_recorded(self) -> None:
        h = await _harness(llm=ScriptedLLM(failure=AIProviderUnavailableError("503")))

        with pytest.raises(GenerationFailedError) as caught:
            await h.ask()

        assert caught.value.code == "GENERATION_FAILED"
        assert isinstance(caught.value.__cause__, AIProviderUnavailableError)
        answer = h.last_answer()
        assert answer.status is MessageStatus.FAILED
        assert answer.failure_stage is FailureStage.GENERATION
        assert answer.failure_code == "GENERATION_FAILED"
        assert answer.stop_reason is StopReason.PROVIDER_ERROR
        assert answer.metrics is not None and answer.metrics.retrieval_ms is not None

    async def test_an_open_circuit_fails_the_same_way(self) -> None:
        h = await _harness(llm=ScriptedLLM(failure=AIProviderCircuitOpenError("open")))
        with pytest.raises(GenerationFailedError):
            await h.ask()

    async def test_rate_limiting_says_busy_and_how_long_to_wait(self) -> None:
        h = await _harness(
            llm=ScriptedLLM(failure=AIProviderRateLimitedError("429", retry_after_seconds=2.5))
        )

        with pytest.raises(GenerationRateLimitedError) as caught:
            await h.ask()

        assert caught.value.code == "GENERATION_RATE_LIMITED"
        assert caught.value.context["retry_after_seconds"] == 3
        assert h.last_answer().failure_code == "GENERATION_RATE_LIMITED"

    async def test_a_malformed_response_is_a_generation_failure(self) -> None:
        h = await _harness(llm=ScriptedLLM(failure=AIProviderResponseInvalidError("junk")))

        with pytest.raises(GenerationFailedError):
            await h.ask()

        assert h.last_answer().stop_reason is StopReason.MALFORMED_RESPONSE

    async def test_an_empty_answer_is_a_malformed_response(self) -> None:
        h = await _harness(llm=ScriptedLLM(text="   "))

        with pytest.raises(GenerationFailedError):
            await h.ask()

        answer = h.last_answer()
        assert answer.status is MessageStatus.FAILED
        assert answer.stop_reason is StopReason.MALFORMED_RESPONSE

    async def test_a_hung_provider_times_out(self) -> None:
        h = await _harness(
            llm=ScriptedLLM(hang_after_chunks=0),
            policy=AnswerPolicy(generation_timeout_seconds=0.05),
        )

        with pytest.raises(GenerationTimeoutError):
            await h.ask()

        answer = h.last_answer()
        assert (answer.status, answer.stop_reason) == (MessageStatus.FAILED, StopReason.TIMEOUT)
        assert answer.failure_code == "GENERATION_TIMEOUT"
        await h.answer.wait_for_background()  # the hung producer was stopped

    async def test_a_timeout_after_some_text_keeps_the_partial_answer(self) -> None:
        h = await _harness(
            llm=ScriptedLLM(hang_after_chunks=3),
            policy=AnswerPolicy(generation_timeout_seconds=0.05),
        )

        events = await h.stream()

        failed = events[-1]
        assert isinstance(failed, AnswerFailed)
        assert isinstance(failed.error, GenerationTimeoutError)
        assert failed.message is not None
        assert failed.message.status is MessageStatus.PARTIAL
        assert failed.message.content
        assert h.last_answer().status is MessageStatus.PARTIAL

    async def test_a_stream_dropped_mid_answer_is_recorded_as_partial(self) -> None:
        h = await _harness(
            llm=ScriptedLLM(failure=AIProviderUnavailableError("reset"), fail_after_chunks=2)
        )

        events = await h.stream()

        deltas = "".join(e.text for e in events if isinstance(e, AnswerDelta))
        failed = events[-1]
        assert isinstance(failed, AnswerFailed)
        assert isinstance(failed.error, GenerationFailedError)
        answer = h.last_answer()
        assert answer.status is MessageStatus.PARTIAL
        assert answer.stop_reason is StopReason.PROVIDER_ERROR
        assert answer.failure_code == "GENERATION_FAILED"
        assert answer.content and deltas.startswith(answer.content[:10])

    async def test_running_out_of_output_tokens_is_a_partial_answer_not_an_error(self) -> None:
        h = await _harness(llm=ScriptedLLM(finish_reason=FinishReason.LENGTH))

        message = await h.ask()

        assert message.status is MessageStatus.PARTIAL
        assert message.stop_reason is StopReason.MAX_TOKENS
        assert message.failure_code is None

    async def test_a_defect_propagates_as_itself_and_is_recorded(self) -> None:
        h = await _harness(llm=ScriptedLLM(failure=RuntimeError("HTTP 400")))

        with pytest.raises(RuntimeError):
            await h.ask()

        assert h.last_answer().failure_code == "INTERNAL_ERROR"

    async def test_the_conversation_continues_after_a_failure(self) -> None:
        llm = ScriptedLLM(failure=AIProviderUnavailableError("503"))
        h = await _harness(llm=llm)
        with pytest.raises(GenerationFailedError):
            await h.ask()

        llm.failure = None
        message = await h.ask()

        assert message.status is MessageStatus.COMPLETE
        assert [m.ordinal for m in h.thread()] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Retrieval failures -- distinct from generation failures
# ---------------------------------------------------------------------------


class TestRetrievalFailures:
    @pytest.mark.parametrize(
        "error",
        [DatabaseUnavailableError("down"), ConnectionError("refused")],
        ids=["database", "network"],
    )
    async def test_a_retrieval_dependency_failure_never_reaches_the_model(
        self, error: Exception
    ) -> None:
        h = await _harness(search=_FailingSearch(error))

        with pytest.raises(RetrievalFailedError) as caught:
            await h.ask()

        assert caught.value.code == "RETRIEVAL_FAILED"
        assert h.llm.calls == 0
        answer = h.last_answer()
        assert answer.status is MessageStatus.FAILED
        assert answer.failure_stage is FailureStage.RETRIEVAL
        assert answer.stop_reason is StopReason.RETRIEVAL_FAILED

    async def test_a_retrieval_defect_propagates_as_itself(self) -> None:
        h = await _harness(search=_FailingSearch(ValueError("bug")))

        with pytest.raises(ValueError, match="bug"):
            await h.ask()

        answer = h.last_answer()
        assert (answer.failure_stage, answer.failure_code) == (
            FailureStage.RETRIEVAL,
            "INTERNAL_ERROR",
        )

    async def test_an_embedding_outage_degrades_retrieval_instead_of_failing(self) -> None:
        h = await _harness(degraded_embeddings=True)

        message = await h.ask()

        assert message.grounding is Grounding.GROUNDED
        assert message.metrics is not None
        assert message.metrics.retrieval_degraded == "semantic_unavailable"

    def test_retrieval_and_generation_failures_are_distinct(self) -> None:
        assert RetrievalFailedError.code != GenerationFailedError.code
        assert not issubclass(RetrievalFailedError, GenerationFailedError)
        assert not issubclass(GenerationFailedError, RetrievalFailedError)
        for error in (RetrievalFailedError, GenerationFailedError, ConversationUnavailableError):
            assert issubclass(error, DependencyUnavailableError)
            assert error.retryable


# ---------------------------------------------------------------------------
# The conversation itself
# ---------------------------------------------------------------------------


class TestConversationFailures:
    async def test_a_question_that_cannot_be_recorded_is_not_answered(self) -> None:
        h = await _harness()
        h.pipeline.uow_factory.state.controls.fail_conversation_writes = DatabaseUnavailableError(
            "down"
        )

        with pytest.raises(ConversationUnavailableError):
            await h.ask()

        assert h.llm.calls == 0

    async def test_an_answer_that_cannot_be_saved_is_reported(self) -> None:
        h = await _harness()
        prepared = await h.answer.prepare(h.ctx, h.conversation_id, QUESTION)
        h.pipeline.uow_factory.state.controls.fail_conversation_writes = DatabaseUnavailableError(
            "down"
        )

        events = [event async for event in h.answer.events(prepared)]

        failed = events[-1]
        assert isinstance(failed, AnswerFailed)
        assert isinstance(failed.error, ConversationUnavailableError)
        # Left PENDING; the next turn closes it as abandoned.
        assert h.last_answer().status is MessageStatus.PENDING

    async def test_a_second_question_waits_for_the_first_answer(self) -> None:
        h = await _harness()
        await h.answer.prepare(h.ctx, h.conversation_id, QUESTION)

        with pytest.raises(AnswerInProgressError):
            await h.answer.prepare(h.ctx, h.conversation_id, "And expenses?")

    async def test_an_answer_abandoned_by_a_dead_request_is_closed_by_the_next_turn(
        self,
    ) -> None:
        h = await _harness()
        abandoned = await h.answer.prepare(h.ctx, h.conversation_id, QUESTION)
        h.pipeline.clock.advance(timedelta(minutes=11))

        message = await h.ask("What needs a receipt?")

        closed = h.stored(abandoned.turn.answer.id)
        assert (closed.status, closed.stop_reason) == (MessageStatus.FAILED, StopReason.ABANDONED)
        assert message.status is MessageStatus.COMPLETE

    async def test_an_invalid_question_leaves_no_trace(self) -> None:
        h = await _harness()
        with pytest.raises(ValidationError):
            await h.ask(" \u200b\n ")
        assert h.thread() == []

    async def test_questions_are_rate_limited_before_anything_is_written(self) -> None:
        settings = build_settings(chat_rate_limit_per_account=1)
        guard = AuthRateLimitGuard(
            InMemoryRateLimiter(), RateLimitPolicy.for_chat(settings), RecordingAuditSink()
        )
        h = await _harness(rate_limit=guard)
        await h.ask()

        with pytest.raises(RateLimitedError):
            await h.ask("And expenses?")

        assert len(h.thread()) == 2

    async def test_history_reaches_the_model_without_stale_handles(self) -> None:
        h = await _harness()
        first = await h.ask()
        assert first.citations

        await h.ask("What needs a receipt?")

        history = [m.content for m in h.llm.prompts[1][1:-1]]
        assert history[0] == QUESTION
        assert "sixteen weeks" in history[1]
        assert "[S" not in history[1]


# ---------------------------------------------------------------------------
# Streaming and cancellation
# ---------------------------------------------------------------------------


class TestStreaming:
    async def test_events_arrive_in_order_and_done_is_authoritative(self) -> None:
        h = await _harness(llm=ScriptedLLM(inner=FakeLLMProvider(fabricate_citations=True)))

        events = await h.stream()

        assert isinstance(events[0], AnswerStarted)
        assert isinstance(events[1], RetrievalCompleted)
        assert events[1].sources > 0
        assert all(isinstance(e, AnswerDelta) for e in events[2:-1])
        done = events[-1]
        assert isinstance(done, AnswerCompleted)
        streamed = "".join(e.text for e in events if isinstance(e, AnswerDelta))
        # Deltas are provisional -- the fabricated handle was streamed -- and
        # the final message is what is kept.
        assert "[S99]" in streamed
        assert "[S99]" not in done.message.content
        assert done.message == h.last_answer()

    async def test_streaming_and_non_streaming_produce_the_same_answer(self) -> None:
        streamed_h = await _harness()
        whole_h = await _harness()

        streamed = (await streamed_h.stream())[-1]
        whole = await whole_h.ask()

        assert isinstance(streamed, AnswerCompleted)
        assert streamed.message.content == whole.content
        assert [c.handle for c in streamed.message.citations] == [c.handle for c in whole.citations]

    async def test_closing_the_stream_mid_answer_records_a_partial_answer(self) -> None:
        h = await _harness(llm=ScriptedLLM(chunk_chars=4))
        prepared = await h.answer.prepare(h.ctx, h.conversation_id, QUESTION)
        events = h.answer.events(prepared)

        received = ""
        async for event in events:
            if isinstance(event, AnswerDelta):
                received += event.text
                if len(received) > 20:
                    break
        await events.aclose()
        await h.answer.wait_for_background()

        answer = h.stored(prepared.turn.answer.id)
        assert answer.status is MessageStatus.PARTIAL
        assert answer.stop_reason is StopReason.CANCELLED
        assert answer.content and received.startswith(answer.content[:10])

    async def test_cancelling_before_any_text_records_a_cancelled_answer(self) -> None:
        h = await _harness(llm=ScriptedLLM(hang_after_chunks=0))
        prepared = await h.answer.prepare(h.ctx, h.conversation_id, QUESTION)

        async def consume() -> None:
            async for _ in h.answer.events(prepared):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await h.answer.wait_for_background()

        answer = h.stored(prepared.turn.answer.id)
        assert (answer.status, answer.stop_reason) == (MessageStatus.FAILED, StopReason.CANCELLED)
        assert answer.failure_code == "ANSWER_CANCELLED"
        # The provider was stopped, not left running.
        assert not h.answer._background
