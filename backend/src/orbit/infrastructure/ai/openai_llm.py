"""OpenAI-compatible chat-completions provider.

Speaks `/chat/completions` over the shared `httpx` client, like the embedding
adapter: no vendor SDK, so no second HTTP stack, retry layer, or release
cadence to track (ADR-0007). Model, endpoint, and key come from configuration.

This adapter makes **one** attempt per call. Retries and the circuit breaker
live in `ResilientLLMProvider`, once, for every provider.

| Response                          | Error                             |
|-----------------------------------|-----------------------------------|
| connect/read timeout              | `AIProviderTimeoutError`          |
| connection failure, dropped stream| `AIProviderUnavailableError`      |
| 429                               | `AIProviderRateLimitedError`      |
| 429 `insufficient_quota`          | `ConfigurationError`              |
| 408, 409, 5xx                     | `AIProviderUnavailableError`      |
| 401, 403, 404                     | `ConfigurationError`              |
| other 4xx                         | `RuntimeError` (ORBIT's defect)   |
| 200 that cannot be parsed         | `AIProviderResponseInvalidError`  |
| stream ending without `[DONE]`    | `AIProviderUnavailableError`      |

A stream that stops early is an **error**, never a short answer: the use case
must be able to tell "the model finished" from "the connection dropped".
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from orbit.core.metrics import LLM_FIRST_TOKEN
from orbit.domain.errors import (
    AIProviderRateLimitedError,
    AIProviderResponseInvalidError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ConfigurationError,
)
from orbit.domain.llm import (
    Completion,
    FinishReason,
    LLMMessage,
    StreamEnd,
    StreamEvent,
    TextDelta,
    TokenUsage,
)
from orbit.infrastructure.ai.openai_http import error_code, retry_after_seconds
from orbit.infrastructure.ai.telemetry import (
    correlation_headers,
    provider_call,
    provider_request_id,
    record_tokens,
)

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_REQUEST_TIMEOUT = 408
_HTTP_CONFLICT = 409
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500

_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
}
_DATA_PREFIX = "data:"
_DONE = "[DONE]"


class OpenAILLMProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str,
        model: str,
        context_window: int,
    ) -> None:
        self._client = client
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._context_window = context_window

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window

    def _body(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stream:
            body["stream"] = True
            # The final chunk then carries token usage, which is otherwise
            # not reported for streamed responses at all.
            body["stream_options"] = {"include_usage": True}
        return body

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", **correlation_headers()}

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        with provider_call(operation="chat_complete", model=self._model):
            try:
                response = await self._client.post(
                    self._endpoint,
                    headers=self._headers,
                    json=self._body(
                        messages, max_tokens=max_tokens, temperature=temperature, stream=False
                    ),
                )
            except httpx.TimeoutException as exc:
                msg = "The language model did not respond in time."
                raise AIProviderTimeoutError(msg, error=type(exc).__name__) from exc
            except httpx.TransportError as exc:
                msg = "The language model could not be reached."
                raise AIProviderUnavailableError(msg, error=type(exc).__name__) from exc

            self._raise_for_status(response)
            try:
                payload = response.json()
            except ValueError as exc:
                raise _malformed() from exc
            completion = self._completion(payload)
            record_tokens(
                "chat_complete",
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
            )
            return completion

    async def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion, timed and counted as one provider call.

        The duration covers the whole stream, so a slow generation shows as a
        slow call; time-to-first-token is recorded separately, because it is
        what a user actually waits for.
        """
        began = time.perf_counter()
        seen_first_token = False
        with provider_call(operation="chat_stream", model=self._model):
            async for event in self._stream_events(
                messages, max_tokens=max_tokens, temperature=temperature
            ):
                if isinstance(event, TextDelta) and not seen_first_token:
                    seen_first_token = True
                    LLM_FIRST_TOKEN.observe(time.perf_counter() - began)
                elif isinstance(event, StreamEnd):
                    record_tokens(
                        "chat_stream",
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    )
                yield event

    async def _stream_events(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        body = self._body(messages, max_tokens=max_tokens, temperature=temperature, stream=True)
        finish: FinishReason | None = None
        usage = TokenUsage()
        model = self._model
        try:
            async with self._client.stream(
                "POST", self._endpoint, headers=self._headers, json=body
            ) as response:
                if response.is_error:
                    await response.aread()
                    self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith(_DATA_PREFIX):
                        continue  # blank separators, comments, `event:` lines
                    data = line[len(_DATA_PREFIX) :].strip()
                    if data == _DONE:
                        yield StreamEnd(
                            finish_reason=finish or FinishReason.OTHER, usage=usage, model_id=model
                        )
                        return
                    chunk = _json_object(data)
                    reported = chunk.get("model")
                    model = reported if isinstance(reported, str) else model
                    usage = _usage(chunk.get("usage")) or usage
                    for text, reason in _choice_deltas(chunk):
                        if reason is not None:
                            finish = reason
                        if text:
                            yield TextDelta(text)
        except httpx.TimeoutException as exc:
            msg = "The language model stopped responding."
            raise AIProviderTimeoutError(msg, error=type(exc).__name__) from exc
        except httpx.TransportError as exc:
            msg = "The connection to the language model failed."
            raise AIProviderUnavailableError(msg, error=type(exc).__name__) from exc
        msg = "The language model's stream ended before the answer was complete."
        raise AIProviderUnavailableError(msg, finish_reason=finish.value if finish else None)

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if not response.is_error:
            return
        request_id = provider_request_id(response)
        if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            msg = "The language model provider rejected ORBIT's credentials."
            raise ConfigurationError(msg, status=status, provider_request_id=request_id)
        if status == _HTTP_NOT_FOUND:
            msg = "The language model provider does not recognise the configured model."
            raise ConfigurationError(
                msg, status=status, model=self._model, provider_request_id=request_id
            )
        if status == _HTTP_TOO_MANY_REQUESTS:
            if error_code(response) == "insufficient_quota":
                msg = "The language model provider account has exhausted its quota."
                raise ConfigurationError(msg, status=status, provider_request_id=request_id)
            msg = "The language model provider is rate limiting requests."
            raise AIProviderRateLimitedError(
                msg,
                status=status,
                retry_after_seconds=retry_after_seconds(response),
                provider_request_id=request_id,
            )
        if status >= _HTTP_SERVER_ERROR or status in (_HTTP_REQUEST_TIMEOUT, _HTTP_CONFLICT):
            msg = "The language model provider is unavailable."
            raise AIProviderUnavailableError(
                msg,
                status=status,
                retry_after_seconds=retry_after_seconds(response),
                provider_request_id=request_id,
            )
        # The body may echo the prompt -- document text -- so only the status
        # and the provider's error code are kept.
        msg = f"The language model provider rejected the request (HTTP {status})."
        raise RuntimeError(msg + (f" code={error_code(response)}" if error_code(response) else ""))

    def _completion(self, payload: Any) -> Completion:  # noqa: ANN401 -- JSON
        if not isinstance(payload, dict):
            raise _malformed()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _malformed()
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise _malformed()
        model = payload.get("model")
        return Completion(
            text=content,
            finish_reason=_finish_reason(choices[0].get("finish_reason")) or FinishReason.OTHER,
            usage=_usage(payload.get("usage")) or TokenUsage(),
            model_id=model if isinstance(model, str) else self._model,
        )


def _malformed() -> AIProviderResponseInvalidError:
    return AIProviderResponseInvalidError("The language model returned a malformed response.")


def _json_object(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError as exc:
        raise _malformed() from exc
    if not isinstance(chunk, dict):
        raise _malformed()
    return chunk


def _choice_deltas(chunk: dict[str, Any]) -> list[tuple[str | None, FinishReason | None]]:
    choices = chunk.get("choices")
    if choices is None:
        return []
    if not isinstance(choices, list):
        raise _malformed()
    out: list[tuple[str | None, FinishReason | None]] = []
    for choice in choices:
        if not isinstance(choice, dict):
            raise _malformed()
        delta = choice.get("delta")
        text = delta.get("content") if isinstance(delta, dict) else None
        if text is not None and not isinstance(text, str):
            raise _malformed()
        out.append((text, _finish_reason(choice.get("finish_reason"))))
    return out


def _finish_reason(raw: object) -> FinishReason | None:
    if raw is None:
        return None
    return _FINISH_REASONS.get(raw, FinishReason.OTHER) if isinstance(raw, str) else None


def _usage(raw: object) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    return TokenUsage(
        prompt_tokens=prompt if isinstance(prompt, int) and prompt >= 0 else None,
        completion_tokens=completion if isinstance(completion, int) and completion >= 0 else None,
    )
