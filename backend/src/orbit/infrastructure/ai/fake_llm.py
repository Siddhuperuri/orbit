"""A deterministic, offline language model (ADR-0007).

The default provider (`ORBIT_AI_PROVIDER=fake`), not a test stub: the whole
question-answering path -- retrieval, context, generation, citations,
persistence, streaming -- runs with no key and no network.

It answers **extractively from the sources it is shown**: for the sources that
share the most words with the question it quotes the most relevant sentence
and cites that source's handle. With no overlapping source it declares
insufficient evidence exactly as the prompt instructs a real model to. So its
answers are grounded by construction, and deterministic: the same prompt
always yields the same answer, token for token.

`fabricate_citations=True` makes it also cite handles it was never given --
the adversarial behaviour ADR-0006 must defend against, available on demand
so that defence is tested rather than asserted.

Answer *quality* under the fake is meaningless; it exists to exercise
behaviour, not to be right.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence

from orbit.domain.answering import INSUFFICIENT_EVIDENCE_MARKER, QUESTION_BLOCK, SOURCE_BLOCK
from orbit.domain.llm import (
    ChatRole,
    Completion,
    FinishReason,
    LLMMessage,
    StreamEnd,
    StreamEvent,
    TextDelta,
    TokenUsage,
)
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter

FAKE_LLM_MODEL_ID = "orbit-fake-llm-v1"

_WORD = re.compile(r"\w+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Words too common to count as overlap between a question and a source.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)
_MAX_CITED_SOURCES = 2
_MAX_SENTENCE_CHARS = 300


class FakeLLMProvider:
    def __init__(
        self,
        *,
        context_window: int = 16_000,
        fabricate_citations: bool = False,
        stream_chunk_chars: int = 16,
    ) -> None:
        self._context_window = context_window
        self._fabricate = fabricate_citations
        self._chunk_chars = stream_chunk_chars
        self._counter = ApproximateTokenCounter()

    @property
    def model_id(self) -> str:
        return FAKE_LLM_MODEL_ID

    @property
    def context_window(self) -> int:
        return self._context_window

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        del temperature
        text, finish = self._answer(messages, max_tokens=max_tokens)
        return Completion(
            text=text,
            finish_reason=finish,
            usage=self._usage(messages, text),
            model_id=FAKE_LLM_MODEL_ID,
        )

    async def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        del temperature
        text, finish = self._answer(messages, max_tokens=max_tokens)
        for start in range(0, len(text), self._chunk_chars):
            yield TextDelta(text[start : start + self._chunk_chars])
        yield StreamEnd(
            finish_reason=finish, usage=self._usage(messages, text), model_id=FAKE_LLM_MODEL_ID
        )

    # -- the "model" -----------------------------------------------------------

    def _answer(
        self, messages: Sequence[LLMMessage], *, max_tokens: int
    ) -> tuple[str, FinishReason]:
        prompt = next((m.content for m in reversed(messages) if m.role is ChatRole.USER), "")
        question_match = QUESTION_BLOCK.search(prompt)
        question = question_match.group(1) if question_match else ""
        terms = {w for w in _WORD.findall(question.casefold()) if w not in _STOP_WORDS}

        scored: list[tuple[int, int, str, str]] = []
        for position, (handle, body) in enumerate(SOURCE_BLOCK.findall(prompt)):
            overlap = len(terms & set(_WORD.findall(body.casefold())))
            if overlap:
                scored.append((-overlap, position, handle, body))
        scored.sort()

        if not scored:
            text = (
                f"{INSUFFICIENT_EVIDENCE_MARKER} The provided sources do not contain "
                "information that answers this question."
            )
        else:
            claims = [
                f"{_best_sentence(body, terms)} [{handle}]"
                for _, _, handle, body in scored[:_MAX_CITED_SOURCES]
            ]
            text = "According to the provided sources: " + " ".join(claims)
            if self._fabricate:
                # Handles outside the prompt, in both the single and the
                # grouped form, plus a lowercase variant.
                text += f" This is also confirmed by [S99], [{scored[0][2]}, S42] and [s77]."
        return self._truncate(text, max_tokens)

    def _truncate(self, text: str, max_tokens: int) -> tuple[str, FinishReason]:
        if self._counter.count(text) <= max_tokens:
            return text, FinishReason.STOP
        words = text.split(" ")
        while words and self._counter.count(" ".join(words)) > max_tokens:
            words.pop()
        return " ".join(words), FinishReason.LENGTH

    def _usage(self, messages: Sequence[LLMMessage], text: str) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=sum(self._counter.count(m.content) for m in messages),
            completion_tokens=self._counter.count(text),
        )


def _best_sentence(body: str, terms: set[str]) -> str:
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(body.split())) if s.strip()]
    best = max(
        sentences,
        key=lambda s: len(terms & set(_WORD.findall(s.casefold()))),
        default=body.strip(),
    )
    return best if len(best) <= _MAX_SENTENCE_CHARS else best[: _MAX_SENTENCE_CHARS - 1] + "…"
