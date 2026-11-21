from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from evaluator.api.errors import ProviderClientError, TimeoutError
from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.models import ModelProfileInput
from evaluator.provider import ConversationMessage, ProviderHeaderResolver
from evaluator.rate_limits import CentralEndpointLimiter, ProviderCallExecutor, RetryPolicy
from evaluator.streaming import OpenAIStreamingClient, SseParser, _AnthropicAccumulator

pytestmark = pytest.mark.unit


class FakeEndpoints:
    def header_secret_ids(self, endpoint_id):
        return {}


class FakeSecrets:
    def get(self, record_id):
        return None


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def monotonic(self) -> float:
        return next(self.values)

    def now_utc(self):
        return datetime.now(UTC)


class BlockingStream(httpx.SyncByteStream):
    def __init__(self, closed: threading.Event, accepted: threading.Event) -> None:
        self.closed = closed
        self.accepted = accepted

    def __iter__(self):
        self.accepted.set()
        self.closed.wait(2)
        if not self.closed.is_set():
            raise RuntimeError("transport was not closed")
        return
        yield b""  # pragma: no cover


class BlockingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.accepted = threading.Event()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BlockingStream(self.closed, self.accepted))

    def close(self) -> None:
        self.closed.set()


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


class FailingChunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], error: httpx.HTTPError) -> None:
        self.chunks = chunks
        self.error = error

    def __iter__(self):
        yield from self.chunks
        raise self.error


def _endpoint(**changes) -> EndpointRecord:
    now = datetime.now(UTC)
    return EndpointRecord(
        uuid4(),
        EndpointInput(name="p", completions_url="https://provider.test/chat", **changes),
        None,
        now,
        now,
    )


def _client(
    chunks: list[bytes],
    clock: FakeClock,
    limit: int = 10000,
    headers: dict[str, str] | None = None,
    answer_limit: int | None = None,
) -> OpenAIStreamingClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert '"stream": true' in body
        assert '"stream_options": {"include_usage": true}' in body
        return httpx.Response(200, headers=headers, stream=Chunks(chunks))

    resolver = ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {})  # type: ignore[arg-type]
    return OpenAIStreamingClient(
        resolver,
        max_response_bytes=limit,
        max_answer_bytes=answer_limit,
        clock=clock,
        transport=httpx.MockTransport(handler),
    )


def _executor() -> ProviderCallExecutor:
    policy = RetryPolicy(jitter_ratio=0)
    policy.sleep = lambda seconds, cancelled=None: None  # type: ignore[method-assign]
    return ProviderCallExecutor(CentralEndpointLimiter(), policy)


@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
def test_streaming_client_sends_canonical_conversation(api_format: str) -> None:
    messages = [
        ConversationMessage(role="user", content="First"),
        ConversationMessage(role="assistant", content="Answer"),
        ConversationMessage(role="user", content="Next"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        expected = [message.model_dump() for message in messages]
        assert payload["stream"] is True
        if api_format == "openai":
            assert payload["messages"] == [{"role": "system", "content": "System"}, *expected]
            chunks = [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n']
        else:
            assert payload["system"] == "System"
            assert payload["messages"] == expected
            chunks = [
                b'event: content_block_delta\ndata: {"type":"content_block_delta",'
                b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            ]
        return httpx.Response(200, stream=Chunks(chunks))

    resolver = ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {})  # type: ignore[arg-type]
    client = OpenAIStreamingClient(
        resolver,
        max_response_bytes=10_000,
        clock=FakeClock([1.0, 1.1, 1.2]),
        transport=httpx.MockTransport(handler),
    )
    result = client.complete(
        _endpoint(api_format=api_format),
        ModelProfileInput(model_id="model", display_name="M", max_tokens=32),
        messages=messages,
        system_prompt="System",
    )
    assert result.content == "ok"


def test_anthropic_stream_maps_text_stop_and_usage() -> None:
    chunks = [
        b'data: {"type":"message_start","message":{"usage":'
        b'{"input_tokens":3,"output_tokens":0}}}\n\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\n\n',
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        b'"usage":{"output_tokens":2}}\n\n',
        b'data: {"type":"message_stop"}\n\n',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert "stream_options" not in payload
        assert payload["system"] == "system"
        return httpx.Response(200, headers={"request-id": "a-1"}, stream=Chunks(chunks))

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 1.1, 1.2]),
        transport=httpx.MockTransport(handler),
    )
    result = client.complete(
        _endpoint(api_format="anthropic"),
        ModelProfileInput(model_id="claude", display_name="Claude", max_tokens=32),
        prompt="prompt",
        system_prompt="system",
    )
    assert result.successful and result.content == "hello"
    assert result.finish_reason == "end_turn" and result.provider_request_id == "a-1"
    assert result.usage is not None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (3, 2)


def test_anthropic_accumulator_handles_optional_events_and_rejects_invalid_data() -> None:
    accumulator = _AnthropicAccumulator(FakeClock([1.1]), 1.0, 100)
    accumulator.event('{"type":"ping"}')
    accumulator.event('{"type":"content_block_start"}')
    accumulator.event('{"type":"content_block_stop"}')
    accumulator.event('{"type":"message_start","message":{}}')
    accumulator.event('{"type":"content_block_delta","delta":{"type":"input_json_delta"}}')
    accumulator.event('{"type":"message_delta","delta":{}}')
    accumulator.event('{"type":"message_stop"}')
    accumulator.event("[DONE]")
    assert accumulator.done

    invalid_events = (
        "[DONE]",
        "{}",
        '{"type":"message_start","message":[]}',
        '{"type":"content_block_delta","delta":[]}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":1}}',
        '{"type":"message_delta","delta":[]}',
        '{"type":"message_delta","delta":{"stop_reason":1}}',
        '{"type":"message_delta","delta":{},"usage":[]}',
        '{"type":"message_delta","delta":{},"usage":{"output_tokens":-1}}',
        '{"type":"future_event"}',
    )
    for event in invalid_events:
        with pytest.raises(ValueError):
            _AnthropicAccumulator(FakeClock([]), 1.0, 100).event(event)


def test_anthropic_accumulator_enforces_answer_limit() -> None:
    accumulator = _AnthropicAccumulator(FakeClock([]), 1.0, 2)
    with pytest.raises(OverflowError):
        accumulator.event(
            '{"type":"content_block_delta","delta":{"type":"text_delta","text":"long"}}'
        )


def test_anthropic_stream_rejects_provider_error_event() -> None:
    chunks = [b'data: {"type":"error","error":{"type":"overloaded_error"}}\n\n']

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Chunks(chunks))

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 2.0]),
        transport=httpx.MockTransport(handler),
    )
    result = client.complete(
        _endpoint(api_format="anthropic"),
        ModelProfileInput(model_id="claude", display_name="Claude", max_tokens=32),
        prompt="prompt",
    )
    assert not result.successful and result.error_code == "invalid_stream"


def test_timeout_before_response_data_retries_until_exhausted() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 2.0, 3.0]),
        transport=httpx.MockTransport(handler),
    )
    endpoint = _endpoint(max_retries=2)

    with pytest.raises(TimeoutError, match="before response data"):
        _executor().execute(
            endpoint,
            lambda: client.complete(
                endpoint, ModelProfileInput(model_id="m", display_name="M"), prompt="p"
            ),
        )

    assert attempts == 3


def test_network_failure_before_response_data_is_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("disconnected", request=request)
        return httpx.Response(200, stream=Chunks([b"data: [DONE]\n\n"]))

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 2.0, 3.0]),
        transport=httpx.MockTransport(handler),
    )
    endpoint = _endpoint(max_retries=1)
    result = _executor().execute(
        endpoint,
        lambda: client.complete(
            endpoint, ModelProfileInput(model_id="m", display_name="M"), prompt="p"
        ),
    )

    assert attempts == 2
    assert result.successful


def test_timeout_after_partial_stream_is_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        error = httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            200,
            stream=FailingChunks(
                [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'], error
            ),
        )

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 1.1, 1.2]),
        transport=httpx.MockTransport(handler),
    )
    endpoint = _endpoint(max_retries=2)
    result = _executor().execute(
        endpoint,
        lambda: client.complete(
            endpoint, ModelProfileInput(model_id="m", display_name="M"), prompt="p"
        ),
    )

    assert attempts == 1
    assert result.content == "partial"
    assert result.error_code == "timeout"
    assert not result.successful


def test_rate_limit_and_server_errors_remain_retryable() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429)
        if attempts == 2:
            return httpx.Response(503)
        return httpx.Response(200, stream=Chunks([b"data: [DONE]\n\n"]))

    client = OpenAIStreamingClient(
        ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {}),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0, 2.0, 3.0, 4.0]),
        transport=httpx.MockTransport(handler),
    )
    endpoint = _endpoint(max_retries=2)
    result = _executor().execute(
        endpoint,
        lambda: client.complete(
            endpoint, ModelProfileInput(model_id="m", display_name="M"), prompt="p"
        ),
    )

    assert attempts == 3
    assert result.successful


def test_close_interrupts_blocked_provider_response() -> None:
    transport = BlockingTransport()
    resolver = ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {})  # type: ignore[arg-type]
    client = OpenAIStreamingClient(
        resolver,
        max_response_bytes=10000,
        transport=transport,
    )
    completed = threading.Event()

    def call() -> None:
        client.complete(
            _endpoint(),
            ModelProfileInput(model_id="m", display_name="M"),
            prompt="p",
        )
        completed.set()

    thread = threading.Thread(target=call)
    thread.start()
    assert transport.accepted.wait(1)

    started = time.monotonic()
    client.close()
    thread.join(1)

    assert completed.is_set()
    assert time.monotonic() - started < 0.5


def test_diagnostic_mode_attaches_provider_error_without_exposing_header_secret() -> None:
    secret = "header-secret-value"

    class Resolver:
        def resolve(self, endpoint):
            return {"Authorization": secret, "Content-Type": "application/json"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"content-type": "application/json", "x-request-id": "provider-42"},
            json={
                "error": {
                    "message": f"bad model and leaked {secret}",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "unsupported_value",
                }
            },
        )

    client = OpenAIStreamingClient(
        Resolver(),  # type: ignore[arg-type]
        max_response_bytes=10000,
        clock=FakeClock([1.0]),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderClientError) as captured:
        client.complete(
            _endpoint(),
            ModelProfileInput(model_id="m", display_name="M"),
            prompt="p",
            diagnostic_mode=True,
        )
    diagnostic = captured.value.provider_diagnostic
    assert diagnostic.provider_code == "unsupported_value"
    assert diagnostic.provider_request_id == "provider-42"
    assert diagnostic.raw_response is not None
    assert secret not in diagnostic.raw_response
    assert "[REDACTED]" in diagnostic.raw_response


def test_sse_parser_handles_utf8_and_line_fragmentation_multiline_and_comments() -> None:
    parser = SseParser()
    raw = ': ping\r\ndata: {"text":"h\u00e9"}\r\n\r\ndata: one\ndata: two\n\n'.encode()
    parts = [raw[:22], raw[22:25], raw[25:40], raw[40:]]
    events = []
    for part in parts:
        events.extend(parser.feed(part))
    events.extend(parser.finish())
    assert events == ['{"text":"hé"}', "one\ntwo"]


def test_stream_assembles_content_usage_and_exact_metrics() -> None:
    events = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"hé"}}]}\n\n'.encode(),
        b'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}]}\n\n',
        (
            b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":5,'
            b'"total_tokens":9,"prompt_tokens_details":{"cached_tokens":2},'
            b'"completion_tokens_details":{"reasoning_tokens":1}}}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]
    result = _client(
        events, FakeClock([10.0, 10.2, 10.7, 11.0]), headers={"X-Request-ID": "r1"}
    ).complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    assert result.content == "héllo" and result.successful and result.evaluate
    assert result.provider_request_id == "r1"
    assert result.ttft_ms == Decimal("200.0")
    assert result.total_latency_ms == Decimal("1000.0")
    assert result.generation_duration_ms == Decimal("500.0")
    assert result.tokens_per_second == Decimal("10")
    assert result.usage is not None
    assert result.usage.prompt_tokens_details == {"cached_tokens": 2}
    assert result.usage.completion_tokens_details == {"reasoning_tokens": 1}


def test_content_filter_preserves_partial_content_but_prevents_evaluation() -> None:
    result = _client(
        [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\n',
        ],
        FakeClock([1.0, 1.1, 1.2]),
    ).complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    assert result.content == "partial"
    assert not result.successful and not result.evaluate
    assert result.error_code == "provider_guardrail"
    assert result.outcome_category == "provider_guardrail"
    assert result.provider_evidence.as_dict() == {"finish_reason": "content_filter"}


def test_provider_finish_is_valid_without_done_but_unexpected_eof_is_not() -> None:
    finished = _client(
        [b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'],
        FakeClock([1.0, 1.1, 1.2]),
    ).complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    broken = _client(
        [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'], FakeClock([1.0, 1.1, 1.2])
    ).complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    assert finished.successful is True
    assert broken.content == "partial" and broken.successful is False and broken.evaluate is False
    assert broken.error_code == "unexpected_eof"


def test_unknown_ttft_and_tps_without_content_or_positive_generation_duration() -> None:
    no_content = _client([b"data: [DONE]\n\n"], FakeClock([1.0, 1.2])).complete(
        _endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p"
    )
    one_chunk = _client(
        [
            b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
            b"data: [DONE]\n\n",
        ],
        FakeClock([1.0, 1.1, 1.2]),
    ).complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    assert no_content.ttft_ms is None and no_content.tokens_per_second is None
    assert one_chunk.generation_duration_ms is None and one_chunk.tokens_per_second is None


def test_invalid_event_oversized_stream_and_answer_preserve_partial_content() -> None:
    profile = ModelProfileInput(model_id="m", display_name="M")
    invalid = _client(
        [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: nope\n\n"],
        FakeClock([1.0, 1.1, 1.2]),
    ).complete(_endpoint(), profile, prompt="p")
    oversized_answer = _client(
        [
            b'data: {"choices":[{"delta":{"content":"abc"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"def"}}]}\n\n',
        ],
        FakeClock([1.0, 1.1, 1.2]),
        answer_limit=5,
    ).complete(_endpoint(), profile, prompt="p")
    oversized_wire = _client([b"x" * 6], FakeClock([1.0, 1.2]), limit=5).complete(
        _endpoint(), profile, prompt="p"
    )
    assert invalid.content == "ok" and invalid.error_code == "invalid_stream"
    assert oversized_answer.content == "abc" and oversized_answer.error_code == "answer_too_large"
    assert oversized_wire.error_code == "stream_too_large"
    assert not invalid.evaluate and not oversized_answer.evaluate and not oversized_wire.evaluate
