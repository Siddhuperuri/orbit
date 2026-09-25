"""A language model that misbehaves on request.

Wraps the real `FakeLLMProvider` -- so by default it answers exactly as the
fake does -- and records every prompt it is sent. A test can then script the
failures a real provider produces: refusing before any output, dropping the
stream part-way, hanging, answering with scripted text, or stopping for
length.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from orbit.domain.llm import (
    Completion,
    FinishReason,
    LLMMessage,
    StreamEnd,
    StreamEvent,
    TextDelta,
    TokenUsage,
)
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider


@dataclass
class ScriptedLLM:
    inner: FakeLLMProvider = field(default_factory=FakeLLMProvider)
    #: Raised when the call starts -- or, with `fail_after_chunks`, mid-stream.
    failure: Exception | None = None
    fail_after_chunks: int | None = None
    #: Stop producing after this many chunks and never finish.
    hang_after_chunks: int | None = None
    #: Answer with this instead of the fake's own answer.
    text: str | None = None
    finish_reason: FinishReason | None = None
    chunk_chars: int = 12
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(120, 30))
    #: Every prompt received, in call order.
    prompts: list[list[LLMMessage]] = field(default_factory=list)
    #: Set once the first chunk has been produced.
    first_chunk: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def model_id(self) -> str:
        return "scripted-llm"

    @property
    def context_window(self) -> int:
        return self.inner.context_window

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def _script(
        self, messages: Sequence[LLMMessage], max_tokens: int
    ) -> tuple[str, FinishReason]:
        if self.text is not None:
            return self.text, self.finish_reason or FinishReason.STOP
        completion = await self.inner.complete(messages, max_tokens=max_tokens, temperature=0)
        return completion.text, self.finish_reason or completion.finish_reason

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        del temperature
        self.prompts.append(list(messages))
        if self.failure is not None and self.fail_after_chunks is None:
            raise self.failure
        if self.hang_after_chunks is not None:
            await asyncio.Event().wait()
        text, finish = await self._script(messages, max_tokens)
        return Completion(text=text, finish_reason=finish, usage=self.usage, model_id=self.model_id)

    async def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        del temperature
        self.prompts.append(list(messages))
        if self.failure is not None and self.fail_after_chunks is None:
            raise self.failure
        text, finish = await self._script(messages, max_tokens)
        chunks = [text[i : i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)]
        for index, chunk in enumerate(chunks):
            if index == self.hang_after_chunks:
                await asyncio.Event().wait()
            if index == self.fail_after_chunks and self.failure is not None:
                raise self.failure
            yield TextDelta(chunk)
            self.first_chunk.set()
            # A real stream yields control between chunks.
            await asyncio.sleep(0)
        if self.hang_after_chunks is not None and self.hang_after_chunks >= len(chunks):
            await asyncio.Event().wait()
        yield StreamEnd(finish_reason=finish, usage=self.usage, model_id=self.model_id)
