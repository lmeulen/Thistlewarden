from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from evaluator.api.errors import (
    AuthenticationError,
    InvalidProviderResponseError,
    NetworkError,
    ProviderClientError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
)
from evaluator.endpoints import EndpointInput, EndpointRecord, HeaderInput
from evaluator.models import ModelProfileInput
from evaluator.provider import (
    ConversationMessage,
    OpenAIProviderClient,
    ProviderGuardrailError,
    ProviderHeaderResolver,
    attach_provider_diagnostic,
    build_anthropic_completion_request,
    build_chat_completion_request,
    parse_anthropic_completion_response,
    parse_completion_response,
)

pytestmark = pytest.mark.unit


class FakeEndpoints:
    def header_secret_ids(self, endpoint_id):
        return {}


class FakeSecrets:
    def get(self, record_id):
        return None


def _endpoint(url: str = "https://provider.test/chat/completions") -> EndpointRecord:
    now = datetime.now(UTC)
    return EndpointRecord(uuid4(), EndpointInput(name="p", completions_url=url), None, now, now)


def _client(handler, limit: int = 10000) -> OpenAIProviderClient:
    resolver = ProviderHeaderResolver(FakeEndpoints(), FakeSecrets(), {})  # type: ignore[arg-type]
    return OpenAIProviderClient(
        resolver, max_response_bytes=limit, transport=httpx.MockTransport(handler)
    )


def test_header_resolver_composes_api_db_env_and_plain_headers() -> None:
    api_id, header_id = uuid4(), uuid4()

    class Endpoints:
        def header_secret_ids(self, endpoint_id):
            return {"x-secret": header_id}

    class Secret:
        def __init__(self, value):
            self.value = value

    class Secrets:
        def get(self, record_id):
            return Secret("api-key" if record_id == api_id else "header-secret")

    now = datetime.now(UTC)
    endpoint = EndpointRecord(
        uuid4(),
        EndpointInput(
            name="p",
            completions_url="https://provider.test",
            api_key_header="Authorization",
            api_key_prefix="Bearer ",
            extra_headers=[
                HeaderInput(name="X-Secret", value="********", secret=True),
                HeaderInput(name="X-Env", value="${TOKEN}"),
                HeaderInput(name="X-Plain", value="tenant"),
            ],
        ),
        api_id,
        now,
        now,
    )
    headers = ProviderHeaderResolver(Endpoints(), Secrets(), {"TOKEN": "env-secret"}).resolve(
        endpoint
    )  # type: ignore[arg-type]
    assert headers["Authorization"] == "Bearer api-key"
    assert headers["X-Secret"] == "header-secret"
    assert headers["X-Env"] == "env-secret"
    assert headers["X-Plain"] == "tenant"


def test_request_body_composes_messages_optional_fields_reasoning_and_custom() -> None:
    profile = ModelProfileInput(
        model_id="model",
        display_name="M",
        temperature=Decimal("0.25"),
        top_p=None,
        max_tokens=50,
        reasoning_field_path="reasoning.effort",
        reasoning_value="high",
        custom_parameters={"seed": 7},
    )
    body = build_chat_completion_request(profile, prompt="user", system_prompt="system")
    assert body == {
        "model": "model",
        "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
        "seed": 7,
        "temperature": Decimal("0.25"),
        "max_tokens": 50,
        "reasoning": {"effort": "high"},
        "stream": False,
    }


def test_conversation_requests_preserve_roles_order_and_system_placement() -> None:
    profile = ModelProfileInput(model_id="model", display_name="M", max_tokens=128)
    messages = [
        ConversationMessage(role="user", content="First question"),
        ConversationMessage(role="assistant", content="First answer"),
        ConversationMessage(role="user", content="Follow-up"),
        ConversationMessage(role="assistant", content="Second answer"),
        ConversationMessage(role="user", content="Final question"),
    ]

    openai = build_chat_completion_request(
        profile, messages=messages, system_prompt="System instruction"
    )
    anthropic = build_anthropic_completion_request(
        profile, messages=messages, system_prompt="System instruction"
    )

    expected = [message.model_dump() for message in messages]
    assert openai["messages"] == [
        {"role": "system", "content": "System instruction"},
        *expected,
    ]
    assert "system" not in anthropic["messages"]
    assert anthropic["system"] == "System instruction"
    assert anthropic["messages"] == expected


@pytest.mark.parametrize(
    ("messages", "match"),
    [
        ([], "must not be empty"),
        ([ConversationMessage(role="assistant", content="answer")], "must alternate"),
        (
            [
                ConversationMessage(role="user", content="one"),
                ConversationMessage(role="user", content="two"),
            ],
            "must alternate",
        ),
        (
            [
                ConversationMessage(role="user", content="question"),
                ConversationMessage(role="assistant", content="answer"),
            ],
            "must end with a user",
        ),
    ],
)
def test_conversation_request_rejects_invalid_role_sequences(
    messages: list[ConversationMessage], match: str
) -> None:
    profile = ModelProfileInput(model_id="model", display_name="M")
    with pytest.raises(ValueError, match=match):
        build_chat_completion_request(profile, messages=messages)


def test_conversation_request_rejects_ambiguous_or_blank_input() -> None:
    profile = ModelProfileInput(model_id="model", display_name="M")
    message = ConversationMessage(role="user", content="question")
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_chat_completion_request(profile, prompt="question", messages=[message])
    with pytest.raises(ValueError, match="required"):
        build_chat_completion_request(profile)
    with pytest.raises(ValueError, match="blank"):
        build_chat_completion_request(profile, prompt="  ")
    with pytest.raises(ValueError, match="system prompt"):
        build_chat_completion_request(profile, prompt="question", system_prompt="  ")


@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
def test_non_streaming_client_sends_canonical_conversation(api_format: str) -> None:
    messages = [
        ConversationMessage(role="user", content="First"),
        ConversationMessage(role="assistant", content="Answer"),
        ConversationMessage(role="user", content="Next"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        expected = [message.model_dump() for message in messages]
        if api_format == "openai":
            assert payload["messages"] == [{"role": "system", "content": "System"}, *expected]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            )
        assert payload["system"] == "System"
        assert payload["messages"] == expected
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
        )

    endpoint = _endpoint()
    endpoint = EndpointRecord(
        endpoint.id,
        endpoint.data.model_copy(update={"api_format": api_format}),
        None,
        endpoint.created_at,
        endpoint.updated_at,
    )
    result = _client(handler).complete(
        endpoint,
        ModelProfileInput(model_id="model", display_name="M", max_tokens=32),
        messages=messages,
        system_prompt="System",
    )
    assert result.content == "ok"


def test_anthropic_request_and_response_contract() -> None:
    profile = ModelProfileInput(
        model_id="claude", display_name="Claude", max_tokens=128, temperature=Decimal("0.2")
    )
    body = build_anthropic_completion_request(profile, prompt="user", system_prompt="system")
    assert body == {
        "model": "claude",
        "messages": [{"role": "user", "content": "user"}],
        "system": "system",
        "max_tokens": 128,
        "temperature": Decimal("0.2"),
        "stream": False,
    }
    result = parse_anthropic_completion_response(
        b'{"content":[{"type":"text","text":"hello"},{"type":"text","text":" world"}],'
        b'"stop_reason":"end_turn","usage":{"input_tokens":4,"output_tokens":2}}',
        {"request-id": "anthropic-1"},
    )
    assert result.content == "hello world"
    assert result.finish_reason == "end_turn"
    assert result.provider_request_id == "anthropic-1"
    assert result.usage is not None and result.usage.total_tokens == 6


def test_anthropic_refusal_is_a_guardrail_and_non_text_blocks_are_rejected() -> None:
    result = parse_anthropic_completion_response(b'{"content":[],"stop_reason":"refusal"}', {})
    assert not result.successful and result.error_code == "provider_guardrail"
    with pytest.raises(InvalidProviderResponseError):
        parse_anthropic_completion_response(
            b'{"content":[{"type":"tool_use","id":"secret"}],"stop_reason":"tool_use"}',
            {},
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"{}",
        b'{"content":{}}',
        b'{"content":[1]}',
        b'{"content":[{"type":"text","text":1}]}',
        b'{"content":[],"stop_reason":1}',
        b'{"content":[],"usage":[]}',
        b'{"content":[],"usage":{"input_tokens":1}}',
    ],
)
def test_anthropic_response_rejects_invalid_contract(payload: bytes) -> None:
    with pytest.raises(InvalidProviderResponseError):
        parse_anthropic_completion_response(payload, {})


def test_anthropic_client_dispatches_by_endpoint_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["system"] == "system"
        assert payload["messages"] == [{"role": "user", "content": "prompt"}]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    endpoint = _endpoint()
    endpoint = EndpointRecord(
        endpoint.id,
        endpoint.data.model_copy(update={"api_format": "anthropic"}),
        None,
        endpoint.created_at,
        endpoint.updated_at,
    )
    result = _client(handler).complete(
        endpoint,
        ModelProfileInput(model_id="claude", display_name="Claude", max_tokens=32),
        prompt="prompt",
        system_prompt="system",
    )
    assert result.content == "ok"


def test_anthropic_requires_max_tokens() -> None:
    with pytest.raises(ProviderClientError, match="maximum output token"):
        build_anthropic_completion_request(
            ModelProfileInput(model_id="claude", display_name="Claude"), prompt="p"
        )
    with pytest.raises(ProviderClientError, match="only the max_tokens"):
        build_anthropic_completion_request(
            ModelProfileInput(
                model_id="claude",
                display_name="Claude",
                max_tokens=10,
                max_tokens_field_path="generation.max_tokens",
            ),
            prompt="p",
        )


def test_response_parser_normalizes_refusal_without_retaining_refusal_text() -> None:
    result = parse_completion_response(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": None, "refusal": "sensitive refusal detail"},
                        "finish_reason": "content_filter",
                    }
                ]
            }
        ).encode(),
        {"X-Request-ID": "guard-1"},
    )
    assert not result.successful and not result.evaluate
    assert result.error_code == "provider_guardrail"
    assert result.content == ""
    assert result.provider_evidence.as_dict() == {
        "finish_reason": "content_filter",
        "provider_request_id": "guard-1",
        "refusal_present": True,
    }
    assert "sensitive" not in repr(result)


def test_unknown_finish_reason_remains_normal_completion() -> None:
    result = parse_completion_response(
        b'{"choices":[{"message":{"content":"ok"},"finish_reason":"future_reason"}]}', {}
    )
    assert result.successful and result.outcome_category == "normal"


def test_response_parser_accepts_usage_and_request_id() -> None:
    result = parse_completion_response(
        json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 1},
                },
            }
        ).encode(),
        {"X-Request-ID": "provider-1"},
    )
    assert result.content == "ok" and result.provider_request_id == "provider-1"
    assert result.usage is not None and result.usage.prompt_tokens == 2


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{}}]}',
        b'{"choices":[{"message":{"content":3}}]}',
    ],
)
def test_response_parser_rejects_malformed_contract(payload: bytes) -> None:
    with pytest.raises(InvalidProviderResponseError):
        parse_completion_response(payload, {})


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (429, RateLimitError),
        (400, ProviderClientError),
        (499, ProviderClientError),
        (500, ProviderServerError),
        (503, ProviderServerError),
        (302, ProviderClientError),
    ],
)
def test_http_status_classification(status: int, error: type[Exception]) -> None:
    client = _client(
        lambda request: httpx.Response(status, headers={"location": "https://evil.test"})
    )
    with pytest.raises(error):
        client.complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")


def test_known_policy_http_code_is_guardrail_but_unknown_code_is_client_error() -> None:
    policy = _client(
        lambda request: httpx.Response(
            400,
            headers={"x-request-id": "policy-1"},
            json={"error": {"code": "content_policy_violation", "message": "private"}},
        )
    )
    with pytest.raises(ProviderGuardrailError) as captured:
        policy.complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")
    assert captured.value.evidence.as_dict() == {
        "provider_code": "content_policy_violation",
        "provider_request_id": "policy-1",
    }
    assert "private" not in repr(captured.value.evidence)

    unknown = _client(lambda request: httpx.Response(400, json={"error": {"code": "future_code"}}))
    with pytest.raises(ProviderClientError):
        unknown.complete(_endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p")

    oversized = _client(
        lambda request: httpx.Response(
            400,
            headers={"content-length": "999999"},
            json={"error": {"code": "content_filter"}},
        )
    )
    with pytest.raises(ProviderClientError) as ordinary:
        oversized.complete(
            _endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p"
        )
    assert not isinstance(ordinary.value, ProviderGuardrailError)


def test_provider_diagnostic_parses_redacts_and_bounds_error_response() -> None:
    secret = "sensitive-api-key"
    body = json.dumps(
        {
            "error": {
                "message": f"Unsupported temperature; trace={secret}",
                "type": "invalid_request_error",
                "param": "temperature",
                "code": "unsupported_value",
            }
        }
    ).encode()
    response = httpx.Response(
        400,
        headers={"content-type": "application/json", "x-request-id": "provider-request-1"},
        content=body,
    )
    error = ProviderClientError()

    attach_provider_diagnostic(error, response, redact_values=(secret,), limit=1000)

    diagnostic = error.provider_diagnostic
    assert diagnostic.status_code == 400
    assert diagnostic.provider_code == "unsupported_value"
    assert diagnostic.error_type == "invalid_request_error"
    assert diagnostic.parameter == "temperature"
    assert diagnostic.provider_request_id == "provider-request-1"
    assert diagnostic.message is not None and "[REDACTED]" in diagnostic.message
    assert diagnostic.raw_response is not None and secret not in diagnostic.raw_response
    assert diagnostic.response_truncated is False

    truncated_error = ProviderClientError()
    attach_provider_diagnostic(
        truncated_error,
        httpx.Response(400, headers={"content-type": "text/plain"}, content=b"x" * 100),
        redact_values=(),
        limit=10,
    )
    assert truncated_error.provider_diagnostic.raw_response == "x" * 10
    assert truncated_error.provider_diagnostic.response_truncated is True


def test_provider_diagnostic_ignores_binary_body() -> None:
    response = httpx.Response(
        500, headers={"content-type": "application/octet-stream"}, content=b"binary-secret"
    )
    error = ProviderServerError()
    attach_provider_diagnostic(error, response, redact_values=())
    assert error.provider_diagnostic.raw_response is None
    assert error.provider_diagnostic.message is None


def test_redirect_is_not_followed() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "http://other.test/path"})

    with pytest.raises(ProviderClientError):
        _client(handler).complete(
            _endpoint(), ModelProfileInput(model_id="m", display_name="M"), prompt="p"
        )
    assert calls == ["https://provider.test/chat/completions"]


def test_response_size_limit_from_stream_and_content_length() -> None:
    profile = ModelProfileInput(model_id="m", display_name="M")
    with pytest.raises(InvalidProviderResponseError):
        _client(lambda request: httpx.Response(200, content=b"x" * 11), 10).complete(
            _endpoint(), profile, prompt="p"
        )
    with pytest.raises(InvalidProviderResponseError):
        _client(
            lambda request: httpx.Response(200, headers={"content-length": "11"}, content=b"{}"), 10
        ).complete(_endpoint(), profile, prompt="p")


def test_timeout_and_network_are_classified() -> None:
    profile = ModelProfileInput(model_id="m", display_name="M")

    def timeout(request):
        raise httpx.ReadTimeout("late")

    def network(request):
        raise httpx.ConnectError("down")

    with pytest.raises(TimeoutError):
        _client(timeout).complete(_endpoint(), profile, prompt="p")
    with pytest.raises(NetworkError):
        _client(network).complete(_endpoint(), profile, prompt="p")
