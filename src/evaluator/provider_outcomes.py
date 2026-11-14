"""Stable provider outcome categories and bounded guardrail evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

OutcomeCategory = Literal[
    "normal", "provider_guardrail", "provider_error", "processing_error", "unknown"
]
OutcomeStage = Literal["target", "judge", "evaluator", "pipeline"]

_GUARDRAIL_FINISH_REASONS = frozenset({"content_filter"})
_GUARDRAIL_CODES = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "responsibleaipolicyviolation",
        "safety_violation",
    }
)
_EVIDENCE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """Safe structured evidence; never contains provider message or response body."""

    finish_reason: str | None = None
    provider_code: str | None = None
    error_type: str | None = None
    provider_request_id: str | None = None
    refusal_present: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        values: dict[str, str | bool | None] = {
            "finish_reason": self.finish_reason,
            "provider_code": self.provider_code,
            "error_type": self.error_type,
            "provider_request_id": self.provider_request_id,
            "refusal_present": True if self.refusal_present else None,
        }
        return {key: value for key, value in values.items() if value is not None}


def bounded_scalar(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    return str(value)[:_EVIDENCE_LIMIT]


def is_guardrail_signal(*, finish_reason: str | None, provider_code: str | None) -> bool:
    return bool(
        (finish_reason is not None and finish_reason.lower() in _GUARDRAIL_FINISH_REASONS)
        or (provider_code is not None and provider_code.lower() in _GUARDRAIL_CODES)
    )


def error_evidence(content: bytes, headers: dict[str, str]) -> ProviderEvidence:
    """Extract only allowlisted structured fields from bounded JSON error content."""
    provider_code = error_type = None
    try:
        payload = json.loads(content)
        detail = payload.get("error", payload) if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            provider_code = bounded_scalar(detail.get("code"))
            error_type = bounded_scalar(detail.get("type"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    lowered = {key.lower(): value for key, value in headers.items()}
    request_id = next(
        (
            bounded_scalar(lowered[name])
            for name in ("x-request-id", "request-id", "x-ms-request-id")
            if lowered.get(name)
        ),
        None,
    )
    return ProviderEvidence(
        provider_code=provider_code,
        error_type=error_type,
        provider_request_id=request_id,
    )
