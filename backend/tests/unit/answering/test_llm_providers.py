"""The `LLMProvider` adapters and the resilience decorator, with no network.

The OpenAI adapter is driven over `httpx.MockTransport`, so every status,
malformed body, and broken stream a real provider can produce is exercised
without a key. The decorator is driven by a scripted inner provider and a
controllable clock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

import httpx
import pytest

from orbit.domain.answering import INSUFFICIENT_EVIDENCE_MARKER, render_question
from orbit.domain.errors import (
    AIProviderCircuitOpenError,
    AIProviderRateLimitedError,
    AIProviderResponseInvalidError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ConfigurationError,
)
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
from orbit.infrastructure.ai.fake_llm import FAKE_LLM_MODEL_ID, FakeLLMProvider
from orbit.infrastructure.ai.openai_llm import OpenAILLMProvider
from orbit.infrastructure.ai.resilient_llm import (
    CircuitBreaker,
    LLMRetryPolicy,
    ResilientLLMProvider,
)

BASE_URL = "https://llm.example.test/v1"
MESSAGES = [
    LLMMessage(ChatRole.SYSTEM, "Be grounded."),
    LLMMessage(ChatRole.USER, "Hi"),
]


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAILLMProvider:
    return OpenAILLMProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url=BASE_URL,
        api_key="sk-test",
        model="gpt-4o-mini",
        context_window=128_000,
    )


def _sse(*chunks: object, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _delta(text: str | None, finish: str | None = None) -> dict[str, object]:
    return {
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
    }


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in stream]


class TestOpenAIComplete:
    async def test_sends_configured_model_and_parses_answer_usage_and_finish(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert str(request.url) == f"{BASE_URL}/chat/completions"
            assert request.headers["Authorization"] == "Bearer sk-test"
            assert body["model"] == "gpt-4o-mini"
            assert body["max_tokens"] == 50
            assert body["messages"][0] == {"role": "system", "content": "Be grounded."}
            assert "stream" not in body
            return httpx.Response(
                200,
                json={
                    "model": "gpt-4o-mini-2024-07-18",
                    "choices": [{"message": {"content": "Answer [S1]."}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 90, "completion_tokens": 4},
                },
            )

        completion = await _provider(handle).complete(MESSAGES, max_tokens=50, temperature=0)

        assert completion == Completion(
            text="Answer [S1].",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(90, 4),
            model_id="gpt-4o-mini-2024-07-18",
        )

    @pytest.mark.parametrize(
        "body",
        [{}, {"choices": []}, {"choices": [{"message": {}}]}, [], {"choices": "x"}],
    )
    async def test_a_malformed_body_is_a_response_error(self, body: object) -> None:
        provider = _provider(lambda _: httpx.Response(200, json=body))
        with pytest.raises(AIProviderResponseInvalidError):
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_a_non_json_body_is_a_response_error(self) -> None:
        provider = _provider(lambda _: httpx.Response(200, content=b"<html>"))
        with pytest.raises(AIProviderResponseInvalidError):
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_rate_limiting_carries_retry_after(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(429, headers={"retry-after": "7"}, json={"error": {}})
        )
        with pytest.raises(AIProviderRateLimitedError) as caught:
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)
        assert caught.value.context["retry_after_seconds"] == 7.0

    async def test_exhausted_quota_is_configuration_not_rate_limiting(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(429, json={"error": {"code": "insufficient_quota"}})
        )
        with pytest.raises(ConfigurationError):
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_an_error_body_wrapped_in_a_list_is_read_like_a_bare_one(self) -> None:
        # Google's OpenAI-compatible endpoint wraps the error object in a list.
        provider = _provider(
            lambda _: httpx.Response(429, json=[{"error": {"code": "insufficient_quota"}}])
        )
        with pytest.raises(ConfigurationError):
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_reasoning_effort_is_sent_only_when_configured(self) -> None:
        bodies: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "A."}, "finish_reason": "stop"}]},
            )

        await _provider(handle).complete(MESSAGES, max_tokens=50, temperature=0)
        await OpenAILLMProvider(
            httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            base_url=BASE_URL,
            api_key="sk-test",
            model="gemini-3.8-flash",
            context_window=128_000,
            reasoning_effort="low",
        ).complete(MESSAGES, max_tokens=50, temperature=0)

        assert "reasoning_effort" not in bodies[0]
        assert bodies[1]["reasoning_effort"] == "low"

    @pytest.mark.parametrize("status", [408, 409, 500, 502, 503])
    async def test_server_errors_are_transient(self, status: int) -> None:
        provider = _provider(lambda _: httpx.Response(status, json={}))
        with pytest.raises(AIProviderUnavailableError) as caught:
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)
        assert type(caught.value) is AIProviderUnavailableError

    @pytest.mark.parametrize("status", [401, 403, 404])
    async def test_credentials_and_model_errors_are_configuration(self, status: int) -> None:
        provider = _provider(lambda _: httpx.Response(status, json={}))
        with pytest.raises(ConfigurationError):
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_another_client_error_is_orbits_defect_and_does_not_echo_the_body(
        self,
    ) -> None:
        provider = _provider(
            lambda _: httpx.Response(
                400, json={"error": {"code": "context_length_exceeded", "message": "SECRET"}}
            )
        )
        with pytest.raises(RuntimeError) as caught:
            await provider.complete(MESSAGES, max_tokens=50, temperature=0)
        assert "context_length_exceeded" in str(caught.value)
        assert "SECRET" not in str(caught.value)

    async def test_a_timeout_is_a_timeout(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(AIProviderTimeoutError):
            await _provider(handle).complete(MESSAGES, max_tokens=50, temperature=0)

    async def test_a_connection_failure_is_an_outage(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(AIProviderUnavailableError) as caught:
            await _provider(handle).complete(MESSAGES, max_tokens=50, temperature=0)
        assert not isinstance(caught.value, AIProviderTimeoutError)


class TestOpenAIStream:
    async def test_deltas_then_one_end_with_usage(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["stream"] is True
            assert body["stream_options"] == {"include_usage": True}
            return httpx.Response(
                200,
                content=_sse(
                    _delta("Sixteen "),
                    _delta("weeks [S1]."),
                    _delta(None, "stop"),
                    {"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 5}},
                ),
            )

        events = await _collect(_provider(handle).stream(MESSAGES, max_tokens=50, temperature=0))

        assert events == [
            TextDelta("Sixteen "),
            TextDelta("weeks [S1]."),
            StreamEnd(
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(80, 5),
                model_id="gpt-4o-mini-2024-07-18",
            ),
        ]

    async def test_a_truncated_answer_reports_length(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, content=_sse(_delta("Sixteen"), _delta(None, "length")))
        )
        events = await _collect(provider.stream(MESSAGES, max_tokens=50, temperature=0))
        assert isinstance(events[-1], StreamEnd)
        assert events[-1].finish_reason is FinishReason.LENGTH

    async def test_a_stream_that_stops_without_done_is_an_error_not_a_short_answer(
        self,
    ) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, content=_sse(_delta("Sixteen"), done=False))
        )
        received: list[StreamEvent] = []
        with pytest.raises(AIProviderUnavailableError):
            async for event in provider.stream(MESSAGES, max_tokens=50, temperature=0):
                received.append(event)
        assert received == [TextDelta("Sixteen")]

    async def test_a_malformed_chunk_is_a_response_error(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(200, content=b"data: {not json\n\ndata: [DONE]\n\n")
        )
        with pytest.raises(AIProviderResponseInvalidError):
            await _collect(provider.stream(MESSAGES, max_tokens=50, temperature=0))

    async def test_an_error_status_on_a_stream_is_classified_like_any_other(self) -> None:
        provider = _provider(
            lambda _: httpx.Response(429, headers={"retry-after": "2"}, json={"error": {}})
        )
        with pytest.raises(AIProviderRateLimitedError):
            await _collect(provider.stream(MESSAGES, max_tokens=50, temperature=0))

    async def test_a_read_timeout_mid_stream_is_a_timeout(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("stalled", request=request)

        with pytest.raises(AIProviderTimeoutError):
            await _collect(_provider(handle).stream(MESSAGES, max_tokens=50, temperature=0))


# ---------------------------------------------------------------------------
# Resilience decorator
# ---------------------------------------------------------------------------


@dataclass
class _Flaky:
    """Fails with the queued errors, then answers."""

    errors: list[Exception] = field(default_factory=list)
    #: For `stream`: raise the next error after emitting this many deltas.
    fail_after: int = 0
    calls: int = 0

    @property
    def model_id(self) -> str:
        return "flaky"

    @property
    def context_window(self) -> int:
        return 8000

    async def complete(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return Completion("ok", FinishReason.STOP, TokenUsage(), "flaky")

    async def stream(
        self, messages: Sequence[LLMMessage], *, max_tokens: int, temperature: float
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        for n in range(self.fail_after):
            yield TextDelta(f"t{n}")
        if self.errors:
            raise self.errors.pop(0)
        yield TextDelta("ok")
        yield StreamEnd(FinishReason.STOP, TokenUsage(), "flaky")


@dataclass
class _Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def _resilient(
    inner: _Flaky, *, threshold: int = 5, clock: _Clock | None = None, retries: int = 2
) -> tuple[ResilientLLMProvider, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = ResilientLLMProvider(
        inner,
        LLMRetryPolicy(max_retries=retries, base_delay_seconds=0.5, max_wait_seconds=4.0),
        CircuitBreaker(
            failure_threshold=threshold, reset_after_seconds=30, monotonic=clock or _Clock()
        ),
        sleep=sleep,
        random_value=lambda: 0.0,
    )
    return provider, sleeps


class TestRetries:
    async def test_a_transient_failure_is_retried_with_backoff(self) -> None:
        inner = _Flaky([AIProviderUnavailableError("503"), AIProviderTimeoutError("slow")])
        provider, sleeps = _resilient(inner)

        completion = await provider.complete(MESSAGES, max_tokens=10, temperature=0)

        assert completion.text == "ok"
        assert inner.calls == 3
        assert sleeps == [0.25, 0.5]

    async def test_retries_are_bounded(self) -> None:
        inner = _Flaky([AIProviderUnavailableError("503")] * 5)
        provider, _ = _resilient(inner)

        with pytest.raises(AIProviderUnavailableError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        assert inner.calls == 3

    async def test_a_short_retry_after_is_honoured(self) -> None:
        inner = _Flaky([AIProviderRateLimitedError("429", retry_after_seconds=1.5)])
        provider, sleeps = _resilient(inner)

        await provider.complete(MESSAGES, max_tokens=10, temperature=0)

        assert sleeps == [1.5]

    async def test_a_long_retry_after_goes_back_to_the_user(self) -> None:
        inner = _Flaky([AIProviderRateLimitedError("429", retry_after_seconds=60)])
        provider, sleeps = _resilient(inner)

        with pytest.raises(AIProviderRateLimitedError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        assert (inner.calls, sleeps) == (1, [])

    async def test_configuration_errors_are_never_retried(self) -> None:
        inner = _Flaky([ConfigurationError("bad key")])
        provider, _ = _resilient(inner)

        with pytest.raises(ConfigurationError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        assert inner.calls == 1

    async def test_a_stream_is_retried_before_its_first_event(self) -> None:
        inner = _Flaky([AIProviderUnavailableError("503")])
        provider, _ = _resilient(inner)

        events = await _collect(provider.stream(MESSAGES, max_tokens=10, temperature=0))

        assert inner.calls == 2
        assert events[0] == TextDelta("ok")

    async def test_a_stream_is_never_retried_after_text_was_emitted(self) -> None:
        inner = _Flaky([AIProviderUnavailableError("reset")], fail_after=2)
        provider, _ = _resilient(inner)

        received: list[StreamEvent] = []
        with pytest.raises(AIProviderUnavailableError):
            async for event in provider.stream(MESSAGES, max_tokens=10, temperature=0):
                received.append(event)

        assert inner.calls == 1
        assert received == [TextDelta("t0"), TextDelta("t1")]


class TestCircuitBreaker:
    async def test_consecutive_failures_open_the_circuit_and_calls_fail_fast(self) -> None:
        inner = _Flaky([AIProviderUnavailableError("503")] * 3)
        provider, _ = _resilient(inner, threshold=3, retries=0)

        for _ in range(3):
            with pytest.raises(AIProviderUnavailableError):
                await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        with pytest.raises(AIProviderCircuitOpenError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)

        assert inner.calls == 3

    async def test_after_the_cool_down_one_probe_closes_it(self) -> None:
        clock = _Clock()
        inner = _Flaky([AIProviderUnavailableError("503")] * 2)
        provider, _ = _resilient(inner, threshold=2, clock=clock, retries=0)
        for _ in range(2):
            with pytest.raises(AIProviderUnavailableError):
                await provider.complete(MESSAGES, max_tokens=10, temperature=0)

        clock.now += 31
        assert (await provider.complete(MESSAGES, max_tokens=10, temperature=0)).text == "ok"
        assert (await provider.complete(MESSAGES, max_tokens=10, temperature=0)).text == "ok"

    async def test_a_failed_probe_reopens_it(self) -> None:
        clock = _Clock()
        inner = _Flaky([AIProviderUnavailableError("503")] * 3)
        provider, _ = _resilient(inner, threshold=2, clock=clock, retries=0)
        for _ in range(2):
            with pytest.raises(AIProviderUnavailableError):
                await provider.complete(MESSAGES, max_tokens=10, temperature=0)

        clock.now += 31
        with pytest.raises(AIProviderUnavailableError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        with pytest.raises(AIProviderCircuitOpenError):
            await provider.complete(MESSAGES, max_tokens=10, temperature=0)

    async def test_rate_limiting_does_not_open_the_circuit(self) -> None:
        inner = _Flaky([AIProviderRateLimitedError("429")] * 5)
        provider, _ = _resilient(inner, threshold=2, retries=0)

        for _ in range(5):
            with pytest.raises(AIProviderRateLimitedError):
                await provider.complete(MESSAGES, max_tokens=10, temperature=0)
        assert inner.calls == 5

    def test_a_probe_that_never_reports_back_expires(self) -> None:
        clock = _Clock()
        breaker = CircuitBreaker(failure_threshold=1, reset_after_seconds=30, monotonic=clock)
        breaker.record_failure(AIProviderUnavailableError("503"))
        clock.now += 31
        breaker.before_call()  # the probe -- cancelled, never reports

        with pytest.raises(AIProviderCircuitOpenError):
            breaker.before_call()
        clock.now += 31
        breaker.before_call()  # a new probe is allowed


# ---------------------------------------------------------------------------
# The fake provider
# ---------------------------------------------------------------------------


def _prompt(question: str, *sources: tuple[str, str]) -> list[LLMMessage]:
    blocks = "\n\n".join(f'<source id="{h}" title="t">\n{body}\n</source>' for h, body in sources)
    return [
        LLMMessage(ChatRole.SYSTEM, "rules"),
        LLMMessage(ChatRole.USER, f"{blocks}\n\n{render_question(question)}"),
    ]


class TestFakeLLM:
    async def test_it_answers_extractively_and_cites_only_what_it_was_given(self) -> None:
        messages = _prompt(
            "How long is parental leave?",
            ("S1", "Expenses need receipts."),
            ("S2", "Parental leave is sixteen weeks. It is paid."),
        )

        completion = await FakeLLMProvider().complete(messages, max_tokens=500, temperature=0)

        assert completion.text == (
            "According to the provided sources: Parental leave is sixteen weeks. [S2]"
        )
        assert completion.model_id == FAKE_LLM_MODEL_ID
        assert completion.usage.prompt_tokens and completion.usage.completion_tokens

    async def test_it_declares_insufficient_evidence_when_nothing_matches(self) -> None:
        messages = _prompt("Which airline?", ("S1", "Expenses need receipts."))

        completion = await FakeLLMProvider().complete(messages, max_tokens=500, temperature=0)

        assert completion.text.startswith(INSUFFICIENT_EVIDENCE_MARKER)

    async def test_it_is_deterministic_and_streams_the_same_text(self) -> None:
        messages = _prompt("parental leave", ("S1", "Parental leave is sixteen weeks."))
        fake = FakeLLMProvider()

        first = await fake.complete(messages, max_tokens=500, temperature=0)
        second = await fake.complete(messages, max_tokens=500, temperature=0)
        streamed = await _collect(fake.stream(messages, max_tokens=500, temperature=0))

        assert first == second
        assert "".join(e.text for e in streamed if isinstance(e, TextDelta)) == first.text
        assert isinstance(streamed[-1], StreamEnd)

    async def test_it_fabricates_citations_on_demand(self) -> None:
        messages = _prompt("parental leave", ("S1", "Parental leave is sixteen weeks."))

        completion = await FakeLLMProvider(fabricate_citations=True).complete(
            messages, max_tokens=500, temperature=0
        )

        assert "[S99]" in completion.text

    async def test_it_honours_max_tokens(self) -> None:
        messages = _prompt("parental leave", ("S1", "Parental leave is sixteen weeks."))

        completion = await FakeLLMProvider().complete(messages, max_tokens=4, temperature=0)

        assert completion.finish_reason is FinishReason.LENGTH
        assert len(completion.text.split()) <= 4
