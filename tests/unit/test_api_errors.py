from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from evaluator.api.errors import (
    ApiError,
    AuthenticationError,
    CancelledError,
    ConflictError,
    ErrorDetail,
    InternalError,
    InvalidProviderResponseError,
    JudgeParseError,
    NetworkError,
    NotFoundError,
    ProviderClientError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
    ValidationApiError,
    error_payload,
    sanitize_text,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error_type", "code", "status"),
    [
        (ValidationApiError, "VALIDATION_ERROR", 400),
        (NotFoundError, "NOT_FOUND", 404),
        (ConflictError, "CONFLICT", 409),
        (AuthenticationError, "AUTHENTICATION_ERROR", 502),
        (RateLimitError, "RATE_LIMIT_ERROR", 503),
        (ProviderClientError, "PROVIDER_CLIENT_ERROR", 502),
        (ProviderServerError, "PROVIDER_SERVER_ERROR", 503),
        (TimeoutError, "TIMEOUT_ERROR", 503),
        (NetworkError, "NETWORK_ERROR", 503),
        (InvalidProviderResponseError, "INVALID_PROVIDER_RESPONSE", 502),
        (JudgeParseError, "JUDGE_PARSE_ERROR", 502),
        (CancelledError, "CANCELLED", 409),
        (InternalError, "INTERNAL_ERROR", 500),
    ],
)
def test_domain_error_mapping(error_type: type[ApiError], code: str, status: int) -> None:
    error = error_type()

    assert error.code == code
    assert error.status_code == status
    assert error.message


def test_pydantic_validation_error_contains_field_details() -> None:
    class Input(BaseModel):
        name: str = Field(min_length=2)
        count: int = Field(gt=0)

    with pytest.raises(ValidationError) as caught:
        Input.model_validate({"name": "", "count": 0})

    error = ValidationApiError.from_pydantic(caught.value)

    assert [detail.field for detail in error.details] == ["name", "count"]
    assert all(detail.message for detail in error.details)


def test_sanitizer_redacts_all_registered_secrets_longest_first() -> None:
    assert (
        sanitize_text(
            "Authorization: Bearer fixture-secret and fixture",
            ["fixture", "fixture-secret", ""],
        )
        == "Authorization: Bearer [REDACTED] and [REDACTED]"
    )


def test_error_payload_redacts_message_and_details() -> None:
    error = ValidationApiError(
        "Token fixture-secret is invalid",
        details=(ErrorDetail("token", "fixture-secret rejected"),),
    )

    payload = error_payload(error, "request-id", secrets=("fixture-secret",))

    assert payload == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Token [REDACTED] is invalid",
            "details": [{"field": "token", "message": "[REDACTED] rejected"}],
            "request_id": "request-id",
        }
    }
