"""What crosses the `LLMProvider` port: messages in, text and usage out.

Vendor-neutral on purpose. The shapes here are the intersection every chat
model API offers -- role-tagged messages, a text completion, a finish reason,
token counts when the provider reports them -- so an adapter translates at
its edge and nothing upstream learns which wire format was spoken.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: ChatRole
    content: str


class FinishReason(StrEnum):
    """Why the model stopped, normalised across providers."""

    #: It finished its answer.
    STOP = "stop"
    #: It ran out of output budget: the answer is truncated.
    LENGTH = "length"
    #: The provider withheld or cut the output on policy grounds.
    CONTENT_FILTER = "content_filter"
    #: Anything else the provider reported; treated as incomplete.
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """As the provider reported it. `None` means "not reported", never zero."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class Completion:
    """A whole answer, from `LLMProvider.complete`."""

    text: str
    finish_reason: FinishReason
    usage: TokenUsage
    model_id: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    """The next piece of a streamed answer. Never empty."""

    text: str


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """The last event of a stream that finished normally.

    A stream that stops *without* this event was interrupted: the adapter
    raises instead, so a truncated answer can never masquerade as a complete
    one.
    """

    finish_reason: FinishReason
    usage: TokenUsage
    model_id: str


StreamEvent = TextDelta | StreamEnd
