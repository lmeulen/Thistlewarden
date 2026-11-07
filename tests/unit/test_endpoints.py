from __future__ import annotations

import pytest
from pydantic import ValidationError

from evaluator.endpoints import EndpointInput, compose_api_key, validate_header_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name", ["Authorization", "Ocp-Apim-Subscription-Key", "X_Project.Id", "x-custom"]
)
def test_valid_header_names(name: str) -> None:
    assert validate_header_name(name) == name


@pytest.mark.parametrize("name", ["", "Bad Header", "Header:", "ümlaut", "line\nbreak"])
def test_invalid_header_names(name: str) -> None:
    with pytest.raises(ValueError):
        validate_header_name(name)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1/chat/completions",
        "http://localhost:8000/v1/chat/completions",
        "http://127.0.0.1:8000/v1/chat/completions",
        "http://[::1]:8000/v1/chat/completions",
    ],
)
def test_https_and_local_http_are_allowed(url: str) -> None:
    endpoint = EndpointInput(name="Provider", completions_url=url)
    assert endpoint.completions_url == url


def test_external_http_requires_trust_and_confirmation() -> None:
    payload = {"name": "Internal", "completions_url": "http://llm.internal/v1/chat/completions"}
    with pytest.raises(ValidationError):
        EndpointInput.model_validate(payload)
    with pytest.raises(ValidationError):
        EndpointInput.model_validate({**payload, "trusted_internal_http": True})

    result = EndpointInput.model_validate(
        {**payload, "trusted_internal_http": True, "security_warning_confirmed": True}
    )
    assert result.trusted_internal_http is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/model",
        "https://user:password@example.com/model",
        "https://example.com/model#fragment",
        "relative/path",
    ],
)
def test_unsafe_or_non_absolute_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        EndpointInput(name="Provider", completions_url=url)


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_seconds", 0),
        ("max_retries", -1),
        ("requests_per_minute", 0),
        ("tokens_per_minute", -1),
        ("max_concurrency", 0),
    ],
)
def test_invalid_limits_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        EndpointInput.model_validate(
            {"name": "Provider", "completions_url": "https://example.com/v1", field: value}
        )


def test_none_rate_limits_mean_unlimited() -> None:
    value = EndpointInput(name="Provider", completions_url="https://example.com/v1")
    assert value.requests_per_minute is None
    assert value.tokens_per_minute is None
    assert value.max_concurrency is None


def test_prefix_is_combined_without_trimming() -> None:
    assert compose_api_key("Bearer ", "secret") == "Bearer secret"
    assert compose_api_key("", "secret") == "secret"
    endpoint = EndpointInput(
        name="Provider", completions_url="https://example.com/v1", api_key_prefix="  Token "
    )
    assert endpoint.api_key_prefix == "  Token "


def test_duplicate_headers_and_api_header_collision_are_rejected() -> None:
    base = {"name": "Provider", "completions_url": "https://example.com/v1"}
    with pytest.raises(ValidationError):
        EndpointInput.model_validate(
            {
                **base,
                "extra_headers": [
                    {"name": "X-Tenant", "value": "a"},
                    {"name": "x-tenant", "value": "b"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        EndpointInput.model_validate(
            {**base, "api_key_header": "X-Key", "extra_headers": [{"name": "x-key", "value": "a"}]}
        )
