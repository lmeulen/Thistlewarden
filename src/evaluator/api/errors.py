"""Uniform API errors, mappings, and response sanitization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


class ApiError(Exception):
    """Safe domain error that may be rendered to an API client."""

    code = "INTERNAL_ERROR"
    status_code = 500
    default_message = "An internal error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Sequence[ErrorDetail] = (),
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = tuple(details)


class ValidationApiError(ApiError):
    code = "VALIDATION_ERROR"
    status_code = 400
    default_message = "Input is invalid."

    @classmethod
    def from_pydantic(cls, error: ValidationError) -> ValidationApiError:
        details = tuple(
            ErrorDetail(
                field=".".join(str(part) for part in item["loc"]),
                message=str(item["msg"]),
            )
            for item in error.errors(include_url=False, include_input=False)
        )
        return cls(details=details)


class NotFoundError(ApiError):
    code = "NOT_FOUND"
    status_code = 404
    default_message = "Object not found."


class ConflictError(ApiError):
    code = "CONFLICT"
    status_code = 409
    default_message = "Change conflicts with existing data."


class AuthenticationError(ApiError):
    code = "AUTHENTICATION_ERROR"
    status_code = 502
    default_message = "Provider authentication failed."


class RateLimitError(ApiError):
    code = "RATE_LIMIT_ERROR"
    status_code = 503
    default_message = "Provider rate limit was reached."


class ProviderClientError(ApiError):
    code = "PROVIDER_CLIENT_ERROR"
    status_code = 502
    default_message = "Provider rejected request."


class ProviderServerError(ApiError):
    code = "PROVIDER_SERVER_ERROR"
    status_code = 503
    default_message = "Provider is temporarily unavailable."


class TimeoutError(ApiError):
    code = "TIMEOUT_ERROR"
    status_code = 503
    default_message = "Provider request timed out."


class NetworkError(ApiError):
    code = "NETWORK_ERROR"
    status_code = 503
    default_message = "Provider could not be reached."


class InvalidProviderResponseError(ApiError):
    code = "INVALID_PROVIDER_RESPONSE"
    status_code = 502
    default_message = "Provider returned an invalid response."


class JudgeParseError(ApiError):
    code = "JUDGE_PARSE_ERROR"
    status_code = 502
    default_message = "Judge response could not be processed."


class CancelledError(ApiError):
    code = "CANCELLED"
    status_code = 409
    default_message = "Action was cancelled."


class InternalError(ApiError):
    pass


class HttpApiError(ApiError):
    """Safe wrapper for HTTP routing errors."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def sanitize_text(value: str, secrets: Iterable[str]) -> str:
    """Redact registered non-empty secret values from safe error text."""

    sanitized = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def error_payload(
    error: ApiError,
    request_id: str,
    *,
    secrets: Iterable[str] = (),
) -> Mapping[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": sanitize_text(error.message, secrets),
            "details": [
                {
                    "field": detail.field,
                    "message": sanitize_text(detail.message, secrets),
                }
                for detail in error.details
            ],
            "request_id": request_id,
        }
    }
