from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from evaluator.api.errors import ProviderClientError
from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.models import ModelProfileInput
from evaluator.provider import ProviderHeaderResolver
from evaluator.streaming import OpenAIStreamingClient, SseParser

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


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def _endpoint() -> EndpointRecord:
    now = datetime.now(UTC)
    return EndpointRecord(
        uuid4(),
        EndpointInput(name="p", completions_url="https://provider.test/chat"),
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
