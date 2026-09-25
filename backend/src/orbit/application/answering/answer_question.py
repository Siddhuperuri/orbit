"""Answer a question from the workspace's documents (ADR-0022).

```
question ─▶ normalise ─▶ record turn (question + PENDING answer)
         ─▶ hybrid retrieval ─▶ top K ─▶ rerank (pass-through default)
         ─▶ context under a token budget, handles S1..Sn
         ─▶ LLM (streamed or whole) ─▶ resolve handles ─▶ citations from DB records
         ─▶ PENDING answer → COMPLETE / PARTIAL / FAILED, exactly once
```

Two phases, because they fail differently:

* `prepare` records the question and retrieves evidence. Every failure here
  is an ordinary exception -- a 404 for someone else's conversation, a 409
  while another answer is in flight, `RetrievalFailedError` when evidence
  cannot be fetched -- so a streaming endpoint can still answer with a plain
  HTTP error before it commits to a stream.
* `events` generates the answer and yields `AnswerEvent`s ending in exactly
  one `AnswerCompleted` or `AnswerFailed`. The non-streaming path (`execute`)
  consumes the same generator, so there is one implementation of grounding,
  citation, and persistence, and streaming cannot drift from it: the terminal
  event carries the **resolved** answer, which is authoritative over the
  deltas that preceded it.

**Failure is classified by stage.** Retrieval failures raise
`RetrievalFailedError`; generation failures produce `GenerationFailedError`
and its subclasses; failing to record the conversation is
`ConversationUnavailableError`. The underlying provider or driver error is
chained and logged, never returned.

**Every accepted question leaves a terminal answer row.** Failure, timeout,
and cancellation all write one; the write runs in a detached task, so a
client disconnecting mid-answer cannot cancel the record of what happened. If
the process dies instead, the PENDING row is closed as abandoned by the next
question in that conversation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from orbit.application.answering.prompt import (
    PROMPT_VERSION,
    PromptFrame,
    build_messages,
    frame_prompt,
)
from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.application.retrieval.hybrid_search import HybridSearch
from orbit.core.clock import Clock
from orbit.core.logging import get_logger
from orbit.core.metrics import CITATIONS, RAG_ANSWERS, RAG_DURATION
from orbit.domain.access import AccessContext, Permission
from orbit.domain.answering import (
    INSUFFICIENT_EVIDENCE_MARKER,
    BuiltContext,
    build_context,
    classify_grounding,
    context_budget,
    resolve_answer,
)
from orbit.domain.conversations import (
    AnswerMetrics,
    ChatMessage,
    FailureStage,
    FinishedAnswer,
    Grounding,
    MessageStatus,
    StartedTurn,
    StopReason,
)
from orbit.domain.errors import (
    AIProviderRateLimitedError,
    AIProviderResponseInvalidError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ConflictError,
    ConversationUnavailableError,
    GenerationFailedError,
    GenerationRateLimitedError,
    GenerationTimeoutError,
    OrbitError,
    RetrievalFailedError,
)
from orbit.domain.llm import FinishReason, LLMMessage, StreamEnd, TextDelta
from orbit.domain.ports.llm import LLMProvider
from orbit.domain.ports.processing import FailureClassifier, TokenCounter
from orbit.domain.ports.reranking import Reranker
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.retrieval import SearchFilters, SearchQuery, SearchResult, normalize_query

logger = get_logger(__name__)

NO_EVIDENCE_ANSWER = (
    "I couldn't find anything in {scope} that relates to this question, so I can't "
    "answer it from your sources. Try rephrasing it, or check that the relevant "
    "document has been uploaded and has finished processing."
)
INSUFFICIENT_CONTEXT_ANSWER = (
    "The sources I found don't contain enough information to answer this question."
)

_RETRIEVAL_FAILED = "ORBIT couldn't search your documents just now. Please try again shortly."
_GENERATION_FAILED = (
    "Your documents were searched, but the answer could not be generated. Please try again."
)
_GENERATION_TIMEOUT = "The answer took too long to generate. Please try again."
_GENERATION_RATE_LIMITED = "The answering service is busy right now. Please try again shortly."
_EMPTY_ANSWER = "The language model returned an empty answer. Please try again."
_CONVERSATION_UNAVAILABLE = "The conversation could not be saved. Please try again."
_CANCELLED_CODE = "ANSWER_CANCELLED"
_INTERNAL_CODE = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    #: Fused candidates retrieval returns.
    retrieval_top_k: int = 12
    #: Candidates kept after reranking, before context construction.
    rerank_top_k: int = 8
    max_sources: int = 8
    #: Ceiling on source text in one prompt, whatever the model's window.
    max_context_tokens: int = 6000
    #: Reserved for, and requested as, the answer's maximum length.
    max_answer_tokens: int = 800
    temperature: float = 0.1
    #: Headroom for the token estimate's error.
    safety_margin_tokens: int = 256
    #: Earlier messages shown to the model, and the tokens they may use.
    history_messages: int = 6
    max_history_tokens: int = 1500
    #: Wall-clock limit on generation, retries included.
    generation_timeout_seconds: float = 60.0
    #: A PENDING answer older than this belonged to a request that died.
    stale_answer_after: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        if self.stale_answer_after.total_seconds() <= self.generation_timeout_seconds:
            msg = "stale_answer_after must exceed the generation timeout."
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnswerStarted:
    conversation_id: uuid.UUID
    question: ChatMessage
    answer_message_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RetrievalCompleted:
    retrieved: int
    #: Passages placed in the model's context.
    sources: int
    degraded: str | None
    retrieval_ms: int


@dataclass(frozen=True, slots=True)
class AnswerDelta:
    """Generated text, as it arrives. Provisional: citations in it are not
    yet resolved. The terminal event's message is authoritative."""

    text: str


@dataclass(frozen=True, slots=True)
class AnswerCompleted:
    message: ChatMessage


@dataclass(frozen=True, slots=True)
class AnswerFailed:
    #: An `OrbitError` for every anticipated failure; anything else is a
    #: defect and is reported generically.
    error: Exception
    #: The answer as recorded (possibly partial), when it could be recorded.
    message: ChatMessage | None


AnswerEvent = AnswerStarted | RetrievalCompleted | AnswerDelta | AnswerCompleted | AnswerFailed


@dataclass(frozen=True, slots=True)
class PreparedAnswer:
    ctx: AccessContext
    turn: StartedTurn
    question: str
    context: BuiltContext
    messages: tuple[LLMMessage, ...]
    retrieved: int
    degraded: str | None
    retrieval_ms: int
    scoped_to_documents: bool
    #: `monotonic()` when the question was accepted.
    started_at: float


@dataclass(slots=True)
class _Generation:
    started_at: float
    parts: list[str] = field(default_factory=list)
    end: StreamEnd | None = None
    failure: Exception | None = None
    timed_out: bool = False
    cancelled: bool = False
    first_token_ms: int | None = None
    generation_ms: int | None = None
    concluded: bool = False


@dataclass(frozen=True, slots=True)
class _ProducerFailed:
    error: Exception


_Queue = asyncio.Queue[TextDelta | StreamEnd | _ProducerFailed]


class _LeadingMarkerFilter:
    """Hides the insufficient-evidence marker from streamed text.

    The prompt asks for it first, so only the opening characters are held
    back -- just until they either spell the marker or cannot.
    """

    def __init__(self) -> None:
        self._held = ""
        self._decided = False

    def feed(self, text: str) -> str:
        if self._decided:
            return text
        self._held += text
        opening = self._held.lstrip().upper()
        if len(opening) < len(
            INSUFFICIENT_EVIDENCE_MARKER
        ) and INSUFFICIENT_EVIDENCE_MARKER.startswith(opening):
            return ""
        self._decided = True
        held, self._held = self._held, ""
        if opening.startswith(INSUFFICIENT_EVIDENCE_MARKER):
            return held.lstrip()[len(INSUFFICIENT_EVIDENCE_MARKER) :].lstrip()
        return held

    def flush(self) -> str:
        held, self._held = self._held, ""
        self._decided = True
        return "" if held.strip().upper() == INSUFFICIENT_EVIDENCE_MARKER else held


# ---------------------------------------------------------------------------
# The use case
# ---------------------------------------------------------------------------


class AnswerQuestion:
    def __init__(  # noqa: PLR0913 -- the pipeline's collaborators, each a port
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        search: HybridSearch,
        reranker: Reranker,
        llm: LLMProvider,
        token_counter: TokenCounter,
        classifier: FailureClassifier,
        policy: AnswerPolicy,
        clock: Clock,
        rate_limit: AuthRateLimitGuard | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._uow_factory = uow_factory
        self._search = search
        self._reranker = reranker
        self._llm = llm
        self._counter = token_counter
        self._classifier = classifier
        self._policy = policy
        self._clock = clock
        self._rate_limit = rate_limit
        self._monotonic = monotonic
        #: Detached tasks -- producers and final writes -- held so they are
        #: not garbage-collected mid-flight, and so shutdown can await them.
        self._background: set[asyncio.Task[Any]] = set()

    # -- public ----------------------------------------------------------------

    async def execute(  # noqa: PLR0913 -- keyword-only request options
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        question: str,
        *,
        document_ids: Sequence[uuid.UUID] | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> ChatMessage:
        """Answer without streaming. Raises the stage error on failure."""
        prepared = await self.prepare(
            ctx,
            conversation_id,
            question,
            document_ids=document_ids,
            request_id=request_id,
            client_ip=client_ip,
        )
        async for event in self.events(prepared, stream=False):
            if isinstance(event, AnswerCompleted):
                return event.message
            if isinstance(event, AnswerFailed):
                raise event.error
        msg = "The answer stream ended without a terminal event."
        raise RuntimeError(msg)  # pragma: no cover -- `events` always concludes

    async def prepare(  # noqa: PLR0913 -- keyword-only request options
        self,
        ctx: AccessContext,
        conversation_id: uuid.UUID,
        question: str,
        *,
        document_ids: Sequence[uuid.UUID] | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> PreparedAnswer:
        ctx.require(Permission.CHAT_USE)
        # Everything that can reject the request runs before anything is
        # written: an invalid question leaves no trace in the conversation.
        text = normalize_query(question)
        filters = SearchFilters(document_ids=frozenset(document_ids) if document_ids else None)
        if self._rate_limit is not None:
            await self._rate_limit.check(identity=str(ctx.user_id), client_ip=client_ip)

        started_at = self._monotonic()
        turn = await self._start_turn(ctx, conversation_id, text, request_id)

        retrieval_started = self._monotonic()
        try:
            response = await self._search.execute(
                ctx,
                SearchQuery(text=text, limit=self._policy.retrieval_top_k, filters=filters),
                # Already limited above under the `chat` scope, which is the
                # stricter of the two. Charging the search budget as well
                # would mean a user asking questions slowly loses the ability
                # to search -- two costs for one request.
                metered=False,
            )
            results = await self._rerank(text, response.results)
            async with self._uow_factory() as uow:
                history = await uow.conversations.recent_exchanges(
                    ctx,
                    conversation_id,
                    before_ordinal=turn.question.ordinal,
                    limit=self._policy.history_messages,
                )
        except BaseException as exc:
            await self._fail_retrieval(ctx, turn, exc, self._elapsed_ms(retrieval_started))
            if isinstance(exc, Exception) and self._is_dependency_failure(exc):
                raise RetrievalFailedError(_RETRIEVAL_FAILED) from exc
            raise
        retrieval_ms = self._elapsed_ms(retrieval_started)

        frame = frame_prompt(
            text,
            history,
            max_history_tokens=self._policy.max_history_tokens,
            count_tokens=self._counter.count,
        )
        context = self._build_context(results, frame)
        return PreparedAnswer(
            ctx=ctx,
            turn=turn,
            question=text,
            context=context,
            messages=tuple(build_messages(frame, context)),
            retrieved=len(response.results),
            degraded=response.degraded,
            retrieval_ms=retrieval_ms,
            scoped_to_documents=filters.document_ids is not None,
            started_at=started_at,
        )

    async def events(
        self, prepared: PreparedAnswer, *, stream: bool = True
    ) -> AsyncGenerator[AnswerEvent, None]:
        state = _Generation(started_at=self._monotonic())
        producer: asyncio.Task[None] | None = None
        try:
            yield AnswerStarted(
                conversation_id=prepared.turn.question.conversation_id,
                question=prepared.turn.question,
                answer_message_id=prepared.turn.answer.id,
            )
            yield RetrievalCompleted(
                retrieved=prepared.retrieved,
                sources=len(prepared.context.sources),
                degraded=prepared.degraded,
                retrieval_ms=prepared.retrieval_ms,
            )

            if prepared.context.is_empty:
                # Nothing to ground an answer in: the model is not asked.
                # Asking it anyway invites exactly the unsupported answer
                # this system exists to prevent.
                finished = self._without_evidence(prepared)
                yield AnswerDelta(finished.content)
                yield await self._conclude(prepared, state, finished, None)
                return

            queue: _Queue = asyncio.Queue()
            producer = self._detach(self._produce(prepared.messages, queue, stream=stream))
            marker = _LeadingMarkerFilter()
            deadline = asyncio.get_running_loop().time() + self._policy.generation_timeout_seconds

            while state.end is None and state.failure is None and not state.timed_out:
                visible = self._accept(await self._next_item(queue, deadline), state, marker)
                if visible:
                    yield AnswerDelta(visible)
            tail = marker.flush()
            if tail:
                yield AnswerDelta(tail)

            state.generation_ms = self._elapsed_ms(state.started_at)
            finished, error = self._judge(prepared, state)
            yield await self._conclude(prepared, state, finished, error)
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away. What was generated is still recorded --
            # in a detached task, because this one is being cancelled.
            if not state.concluded:
                state.cancelled = True
                state.generation_ms = self._elapsed_ms(state.started_at)
                finished, _ = self._judge(prepared, state)
                state.concluded = True
                self._persist_detached(prepared, finished, None)
            raise
        finally:
            if producer is not None and not producer.done():
                producer.cancel()

    @staticmethod
    async def _next_item(
        queue: _Queue, deadline: float
    ) -> TextDelta | StreamEnd | _ProducerFailed | None:
        """The provider's next event, or `None` once the deadline has passed."""
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(queue.get(), remaining)
        except TimeoutError:
            return None

    def _accept(
        self,
        item: TextDelta | StreamEnd | _ProducerFailed | None,
        state: _Generation,
        marker: _LeadingMarkerFilter,
    ) -> str:
        """Record one provider event; return the text safe to show now."""
        if item is None:
            state.timed_out = True
        elif isinstance(item, _ProducerFailed):
            state.failure = item.error
        elif isinstance(item, StreamEnd):
            state.end = item
        else:
            if state.first_token_ms is None:
                state.first_token_ms = self._elapsed_ms(state.started_at)
            state.parts.append(item.text)
            return marker.feed(item.text)
        return ""

    async def wait_for_background(self) -> None:
        """Await detached work -- final writes, stopped producers. For tests
        and graceful shutdown."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    # -- conversation ------------------------------------------------------------

    async def _start_turn(
        self, ctx: AccessContext, conversation_id: uuid.UUID, text: str, request_id: str | None
    ) -> StartedTurn:
        try:
            async with self._uow_factory() as uow:
                turn = await uow.conversations.start_turn(
                    ctx,
                    conversation_id,
                    question=text,
                    model_id=self._llm.model_id,
                    prompt_version=PROMPT_VERSION,
                    request_id=request_id,
                    stale_before=self._clock.now() - self._policy.stale_answer_after,
                )
                await uow.commit()
        except OrbitError as exc:
            if exc.retryable:
                raise ConversationUnavailableError(_CONVERSATION_UNAVAILABLE) from exc
            raise
        except Exception as exc:
            if self._classifier.classify(exc).is_retryable:
                raise ConversationUnavailableError(_CONVERSATION_UNAVAILABLE) from exc
            raise
        return turn

    # -- retrieval -------------------------------------------------------------

    async def _rerank(self, text: str, results: Sequence[SearchResult]) -> list[SearchResult]:
        reranked = await self._reranker.rerank(text, results, top_k=self._policy.rerank_top_k)
        # A reranker may reorder and cut, never add: only retrieved results
        # passed the workspace and visibility checks.
        retrieved = {result.chunk.id for result in results}
        kept = [result for result in reranked if result.chunk.id in retrieved]
        if len(kept) != len(reranked):
            logger.error(
                "answer.reranker_added_candidates",
                reranker=self._reranker.name,
                dropped=len(reranked) - len(kept),
            )
        return kept[: self._policy.rerank_top_k]

    def _build_context(self, results: Sequence[SearchResult], frame: PromptFrame) -> BuiltContext:
        budget = context_budget(
            context_window=self._llm.context_window,
            reserved_output_tokens=self._policy.max_answer_tokens,
            fixed_prompt_tokens=frame.tokens,
            safety_margin_tokens=self._policy.safety_margin_tokens,
            max_context_tokens=self._policy.max_context_tokens,
        )
        context = build_context(
            results,
            budget=budget,
            max_sources=self._policy.max_sources,
            count_tokens=self._counter.count,
        )
        if results and context.is_empty:
            logger.warning(
                "answer.context_budget_exhausted",
                budget=budget,
                candidates=len(results),
                fixed_prompt_tokens=frame.tokens,
            )
        return context

    async def _fail_retrieval(
        self, ctx: AccessContext, turn: StartedTurn, exc: BaseException, retrieval_ms: int
    ) -> None:
        if isinstance(exc, asyncio.CancelledError):
            stop, code = StopReason.CANCELLED, _CANCELLED_CODE
        elif isinstance(exc, Exception) and self._is_dependency_failure(exc):
            stop, code = StopReason.RETRIEVAL_FAILED, RetrievalFailedError.code
        else:
            stop = StopReason.RETRIEVAL_FAILED
            code = exc.code if isinstance(exc, OrbitError) else _INTERNAL_CODE
        logger.warning(
            "answer.retrieval_failed",
            conversation_id=str(turn.question.conversation_id),
            message_id=str(turn.answer.id),
            failure_code=code,
            error=type(exc).__name__,
            retrieval_ms=retrieval_ms,
        )
        finished = FinishedAnswer(
            status=MessageStatus.FAILED,
            content="",
            stop_reason=stop,
            grounding=None,
            failure_stage=FailureStage.RETRIEVAL,
            failure_code=code,
            model_id=None,
            prompt_tokens=None,
            completion_tokens=None,
            discarded_citation_count=0,
            metrics=AnswerMetrics(retrieval_ms=retrieval_ms),
            citations=(),
        )
        task = self._detach(self._write(ctx, turn.answer.id, finished))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass  # the shielded write carries on; the caller re-raises
        except Exception:
            # Left PENDING, the row is closed as abandoned by the next turn.
            logger.warning("answer.failure_not_recorded", message_id=str(turn.answer.id))

    # -- generation --------------------------------------------------------------

    async def _produce(
        self,
        messages: Sequence[LLMMessage],
        queue: _Queue,
        *,
        stream: bool,
    ) -> None:
        """Drive the provider in a task of its own.

        The provider's stream holds a connection and its own cancellation
        scopes; keeping it in one dedicated task means the deadline and a
        client disconnect cancel it cleanly, and neither ever fires inside the
        consumer's `yield`.
        """
        max_tokens, temperature = self._policy.max_answer_tokens, self._policy.temperature
        try:
            if stream:
                async for event in self._llm.stream(
                    messages, max_tokens=max_tokens, temperature=temperature
                ):
                    if isinstance(event, TextDelta) and not event.text:
                        continue
                    queue.put_nowait(event)
                    if isinstance(event, StreamEnd):
                        return
                msg = "The language model's stream ended without finishing."
                raise AIProviderResponseInvalidError(msg)  # noqa: TRY301
            completion = await self._llm.complete(
                messages, max_tokens=max_tokens, temperature=temperature
            )
            if completion.text:
                queue.put_nowait(TextDelta(completion.text))
            queue.put_nowait(
                StreamEnd(
                    finish_reason=completion.finish_reason,
                    usage=completion.usage,
                    model_id=completion.model_id,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(_ProducerFailed(exc))

    def _judge(
        self, prepared: PreparedAnswer, state: _Generation
    ) -> tuple[FinishedAnswer, Exception | None]:
        """Decide what the generation amounted to."""
        resolved = resolve_answer("".join(state.parts), prepared.context.handle_map())
        text = resolved.text
        if resolved.declared_insufficient and not text:
            text = INSUFFICIENT_CONTEXT_ANSWER
        stop, error = self._stop_reason(state, has_text=bool(text))

        if error is None and stop is StopReason.COMPLETED:
            status = MessageStatus.COMPLETE
        elif text:
            status = MessageStatus.PARTIAL
        else:
            status = MessageStatus.FAILED

        failure_code: str | None = None
        if error is not None:
            failure_code = error.code if isinstance(error, OrbitError) else _INTERNAL_CODE
        elif status is MessageStatus.FAILED:
            failure_code = _CANCELLED_CODE

        end = state.end
        usage = end.usage if end is not None else None
        finished = FinishedAnswer(
            status=status,
            content=text,
            stop_reason=stop,
            grounding=classify_grounding(
                sources_available=len(prepared.context.sources),
                declared_insufficient=resolved.declared_insufficient,
                citations=len(resolved.citations),
            )
            if text
            else None,
            failure_stage=FailureStage.GENERATION if failure_code else None,
            failure_code=failure_code,
            model_id=end.model_id if end is not None else self._llm.model_id,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            discarded_citation_count=len(resolved.discarded_handles),
            metrics=self._metrics(prepared, state),
            citations=resolved.citations if text else (),
        )
        if resolved.discarded_handles:
            # ADR-0006: a first-class quality signal, alertable on its rate.
            logger.warning(
                "answer.citations_discarded",
                message_id=str(prepared.turn.answer.id),
                discarded=len(resolved.discarded_handles),
                handles=list(resolved.discarded_handles)[:10],
                model_id=finished.model_id,
                prompt_version=PROMPT_VERSION,
            )
        return finished, error

    def _stop_reason(
        self, state: _Generation, *, has_text: bool
    ) -> tuple[StopReason, Exception | None]:
        if state.cancelled:
            return StopReason.CANCELLED, None
        if state.timed_out:
            return StopReason.TIMEOUT, GenerationTimeoutError(_GENERATION_TIMEOUT)
        if state.failure is not None:
            return self._provider_failure(state.failure)
        assert state.end is not None  # noqa: S101 -- the loop exits on end, failure, or timeout
        stop = {
            FinishReason.LENGTH: StopReason.MAX_TOKENS,
            FinishReason.CONTENT_FILTER: StopReason.CONTENT_FILTERED,
        }.get(state.end.finish_reason, StopReason.COMPLETED)
        if not has_text:
            if stop is StopReason.COMPLETED:
                stop = StopReason.MALFORMED_RESPONSE
            return stop, GenerationFailedError(_EMPTY_ANSWER, finish_reason=state.end.finish_reason)
        return stop, None

    def _provider_failure(self, failure: Exception) -> tuple[StopReason, Exception]:
        error: Exception
        if isinstance(failure, AIProviderRateLimitedError):
            requested = failure.context.get("retry_after_seconds")
            error = GenerationRateLimitedError(
                _GENERATION_RATE_LIMITED,
                retry_after_seconds=math.ceil(requested)
                if isinstance(requested, (int, float))
                else None,
            )
            stop = StopReason.PROVIDER_ERROR
        elif isinstance(failure, AIProviderTimeoutError):
            error, stop = GenerationTimeoutError(_GENERATION_TIMEOUT), StopReason.TIMEOUT
        elif isinstance(failure, AIProviderResponseInvalidError):
            error, stop = GenerationFailedError(_GENERATION_FAILED), StopReason.MALFORMED_RESPONSE
        elif isinstance(failure, AIProviderUnavailableError):
            error, stop = GenerationFailedError(_GENERATION_FAILED), StopReason.PROVIDER_ERROR
        else:
            # A defect -- ORBIT's request was wrong, or configuration is. It
            # propagates as itself and becomes a 500 with a logged traceback.
            return StopReason.PROVIDER_ERROR, failure
        error.__cause__ = failure
        return stop, error

    def _without_evidence(self, prepared: PreparedAnswer) -> FinishedAnswer:
        grounding = (
            Grounding.NO_EVIDENCE if prepared.retrieved == 0 else Grounding.INSUFFICIENT_EVIDENCE
        )
        content = (
            NO_EVIDENCE_ANSWER.format(
                scope="the selected documents"
                if prepared.scoped_to_documents
                else "this workspace's documents"
            )
            if grounding is Grounding.NO_EVIDENCE
            else INSUFFICIENT_CONTEXT_ANSWER
        )
        return FinishedAnswer(
            status=MessageStatus.COMPLETE,
            content=content,
            stop_reason=StopReason.COMPLETED,
            grounding=grounding,
            failure_stage=None,
            failure_code=None,
            model_id=None,
            prompt_tokens=None,
            completion_tokens=None,
            discarded_citation_count=0,
            metrics=self._metrics(prepared, None),
            citations=(),
        )

    # -- concluding ----------------------------------------------------------------

    async def _conclude(
        self,
        prepared: PreparedAnswer,
        state: _Generation,
        finished: FinishedAnswer,
        error: Exception | None,
    ) -> AnswerCompleted | AnswerFailed:
        state.concluded = True
        task = self._persist_detached(prepared, finished, error)
        # Shielded: if the client disconnects now, the write still lands.
        saved = await asyncio.shield(task)
        message = _as_message(prepared.turn.answer, finished)
        if isinstance(saved, Exception):
            return AnswerFailed(error=saved, message=None)
        if error is not None:
            return AnswerFailed(error=error, message=message)
        return AnswerCompleted(message=message)

    def _persist_detached(
        self, prepared: PreparedAnswer, finished: FinishedAnswer, error: Exception | None
    ) -> asyncio.Task[Exception | None]:
        async def persist() -> Exception | None:
            # Counted before the write, not after it: a database outage is
            # exactly when answers still finish but cannot be saved, and a
            # metric that only counted saved answers would go quiet then.
            self._record_metrics(finished)
            try:
                recorded = await self._write(prepared.ctx, prepared.turn.answer.id, finished)
            except Exception as exc:
                logger.exception(
                    "answer.not_saved",
                    message_id=str(prepared.turn.answer.id),
                    status=finished.status,
                )
                saved: Exception = ConversationUnavailableError(_CONVERSATION_UNAVAILABLE)
                saved.__cause__ = exc
                return saved
            if not recorded:
                logger.error("answer.already_finished", message_id=str(prepared.turn.answer.id))
                return ConflictError("This answer was already closed.")
            self._log_outcome(prepared, finished, error)
            return None

        return self._detach(persist())

    async def _write(
        self, ctx: AccessContext, message_id: uuid.UUID, finished: FinishedAnswer
    ) -> bool:
        async with self._uow_factory() as uow:
            recorded = await uow.conversations.finish_answer(ctx, message_id, finished)
            await uow.commit()
        return recorded

    # -- helpers -------------------------------------------------------------

    def _detach(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def _is_dependency_failure(self, exc: Exception) -> bool:
        if isinstance(exc, OrbitError):
            return exc.retryable
        return self._classifier.classify(exc).is_retryable

    def _elapsed_ms(self, since: float) -> int:
        return max(0, round((self._monotonic() - since) * 1000))

    def _metrics(self, prepared: PreparedAnswer, state: _Generation | None) -> AnswerMetrics:
        return AnswerMetrics(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=state.generation_ms if state else None,
            total_ms=self._elapsed_ms(prepared.started_at),
            first_token_ms=state.first_token_ms if state else None,
            retrieved_count=prepared.retrieved,
            context_tokens=prepared.context.tokens,
            retrieval_degraded=prepared.degraded,
        )

    @staticmethod
    def _record_metrics(finished: FinishedAnswer) -> None:
        """Export what `answer.finished` logs, as series an alert can watch.

        One choke point -- every terminal answer, whatever path produced it,
        passes through `_persist_detached` -- so no exit can forget to be counted.
        """
        RAG_ANSWERS.labels(
            status=finished.status.value,
            stop_reason=finished.stop_reason.value,
            grounding=finished.grounding.value if finished.grounding else "none",
        ).inc()
        timings = finished.metrics
        for stage, milliseconds in (
            ("retrieval", timings.retrieval_ms),
            ("first_token", timings.first_token_ms),
            ("generation", timings.generation_ms),
            ("total", timings.total_ms),
        ):
            if milliseconds is not None:
                RAG_DURATION.labels(stage=stage).observe(milliseconds / 1000)
        # Only citations that could be attributed count: an answer that failed
        # before producing text has neither, and would dilute the ratio.
        if finished.citations:
            CITATIONS.labels(result="resolved").inc(len(finished.citations))
        if finished.discarded_citation_count:
            CITATIONS.labels(result="discarded").inc(finished.discarded_citation_count)

    def _log_outcome(
        self, prepared: PreparedAnswer, finished: FinishedAnswer, error: Exception | None
    ) -> None:
        metrics = finished.metrics
        log = logger.warning if error is not None else logger.info
        log(
            "answer.finished",
            conversation_id=str(prepared.turn.question.conversation_id),
            message_id=str(prepared.turn.answer.id),
            status=finished.status.value,
            stop_reason=finished.stop_reason.value,
            grounding=finished.grounding.value if finished.grounding else None,
            failure_stage=finished.failure_stage.value if finished.failure_stage else None,
            failure_code=finished.failure_code,
            error=type(error.__cause__ or error).__name__ if error is not None else None,
            model_id=finished.model_id,
            prompt_version=PROMPT_VERSION,
            reranker=self._reranker.name,
            retrieved=prepared.retrieved,
            sources=len(prepared.context.sources),
            duplicates_dropped=prepared.context.duplicates_dropped,
            over_budget_dropped=prepared.context.over_budget_dropped,
            context_budget=prepared.context.budget,
            context_tokens=metrics.context_tokens,
            citations=len(finished.citations),
            discarded_citations=finished.discarded_citation_count,
            prompt_tokens=finished.prompt_tokens,
            completion_tokens=finished.completion_tokens,
            retrieval_ms=metrics.retrieval_ms,
            generation_ms=metrics.generation_ms,
            first_token_ms=metrics.first_token_ms,
            total_ms=metrics.total_ms,
            degraded=metrics.retrieval_degraded,
        )


def _as_message(pending: ChatMessage, finished: FinishedAnswer) -> ChatMessage:
    return dataclasses.replace(
        pending,
        content=finished.content,
        status=finished.status,
        model_id=finished.model_id,
        prompt_tokens=finished.prompt_tokens,
        completion_tokens=finished.completion_tokens,
        grounding=finished.grounding,
        stop_reason=finished.stop_reason,
        failure_stage=finished.failure_stage,
        failure_code=finished.failure_code,
        discarded_citation_count=finished.discarded_citation_count,
        metrics=finished.metrics,
        citations=finished.citations,
    )
