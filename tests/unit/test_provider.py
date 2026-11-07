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
    OpenAIProviderClient,
    ProviderHeaderResolver,
    attach_provider_diagnostic,
    build_chat_completion_request,
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
