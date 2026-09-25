"""The language-model port (ADR-0007, ADR-0022).

The rest of ORBIT reaches a chat model only through `LLMProvider`. No use case,
route, or component imports a vendor SDK or builds a vendor request; the
composition root picks the adapter from configuration and wraps it in the same
resilience decorator whatever the vendor.

Failure contract, shared with `EmbeddingProvider`:

* `AIProviderUnavailableError` or a subclass for anything that may clear on its
  own -- `AIProviderTimeoutError`, `AIProviderRateLimitedError` (optionally
  with a `retry_after_seconds` context value), a 5xx, a dropped connection,
  `AIProviderResponseInvalidError` for a body that cannot be used.
* `ConfigurationError` for what retrying cannot fix: rejected credentials,
  exhausted quota, an unknown model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from orbit.domain.llm import Completion, LLMMessage, StreamEvent


class LLMProvider(Protocol):
    @property
    def model_id(self) -> str:
        """The model actually answering. Recorded on every assistant message."""
        ...

    @property
    def context_window(self) -> int:
        """Most tokens -- prompt plus output -- one request may use.

        Declared by the provider so the context budget is computed from the
        real limit rather than assumed.
        """
        ...

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        """The whole answer in one response."""
        ...

    def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        """The answer as it is generated: `TextDelta`s, then exactly one
        `StreamEnd`. An interrupted stream raises rather than ending quietly."""
        ...
