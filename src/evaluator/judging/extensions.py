"""Public, versioned contracts for trusted local LLM-judge extensions."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from decimal import Decimal
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.evaluators import EvaluationResult
from evaluator.judging.presets import JudgePreset

__all__ = [
    "BUILTIN_JUDGE_ID",
    "BUILTIN_JUDGE_VERSION",
    "EmptyJudgeConfiguration",
    "JUDGE_EXTENSION_API_VERSION",
    "JudgeCallEvidenceError",
    "JudgeCancelledError",
    "JudgeCriterionDecision",
    "JudgeCriterionSnapshot",
    "JudgeDecision",
    "JudgeDecisionError",
    "JudgeEvaluationRequest",
    "JudgeExecutionError",
    "JudgeExtensionError",
    "JudgeImplementation",
    "JudgeImplementationRegistry",
    "JudgeImplementationUnavailable",
    "JudgeMessage",
    "JudgePresetSnapshot",
    "JudgeProvider",
    "JudgeProviderResult",
    "JudgeProviderUsage",
    "JudgeRegistrationError",
    "MAX_EXTENSION_METADATA_BYTES",
    "RegistryHook",
    "canonical_messages",
    "validate_decision",
]

JUDGE_EXTENSION_API_VERSION = "1.0"
BUILTIN_JUDGE_ID = "openai-compatible"
BUILTIN_JUDGE_VERSION = "1.0"
MAX_EXTENSION_METADATA_BYTES = 32 * 1024
_IMPLEMENTATION_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")


class JudgeExtensionError(RuntimeError):
    """Stable, content-free extension failure safe for logs and persisted error types."""


class JudgeRegistrationError(JudgeExtensionError):
    """Raised for invalid, duplicate, late, or incompatible registration."""


class JudgeImplementationUnavailable(JudgeExtensionError):
    """Raised when an immutable profile references unavailable extension code."""


class JudgeDecisionError(JudgeExtensionError):
    """Raised when extension output violates core-owned invariants."""


class JudgeCallEvidenceError:
    """Safe evidence attached to failures after one or more completed provider calls."""

    def __init__(
        self,
        *,
        provider_calls: tuple[dict[str, Any], ...] = (),
        judge_cost: Decimal = Decimal(0),
    ) -> None:
        self.provider_calls = provider_calls
        self.judge_cost = judge_cost


class JudgeExecutionError(JudgeCallEvidenceError, JudgeExtensionError):
    """Sanitized failure with optional safe evidence from completed provider calls."""

    def __init__(
        self,
        message: str,
        *,
        provider_calls: tuple[dict[str, Any], ...] = (),
        judge_cost: Decimal = Decimal(0),
    ) -> None:
        JudgeExtensionError.__init__(self, message)
        JudgeCallEvidenceError.__init__(self, provider_calls=provider_calls, judge_cost=judge_cost)


class JudgeCancelledError(JudgeCallEvidenceError, CancelledError):
    """Cancellation carrying safe evidence from calls completed before cancellation."""


class JudgeMessage(BaseModel):
    """One canonical text message from original target-model conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class JudgeCriterionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    description: str
    weight: Decimal = Field(ge=0)


class JudgePresetSnapshot(BaseModel):
    """Immutable semantic preset supplied to implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    version: str
    name: str
    description: str
    threshold: Decimal = Field(ge=0, le=1)
    rubric: tuple[JudgeCriterionSnapshot, ...]
    requires_expected: bool = False
    requires_context: bool = False

    @classmethod
    def from_preset(cls, preset: JudgePreset) -> JudgePresetSnapshot:
        return cls.model_validate(preset.model_dump(mode="python"))


class JudgeEvaluationRequest(BaseModel):
    """Secret-free, immutable input visible to trusted judge code."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[JudgeMessage, ...] = Field(min_length=1)
    expected_result: str | None = None
    actual_result: str
    context: str | None = None
    preset: JudgePresetSnapshot
    # Core-owned wrapper applies these user-authored instructions as data.
    instructions: str | None = None
    metric_instructions: Mapping[str, str] = Field(default_factory=dict)
    mode: Literal["single_prompt", "per_metric"] = "single_prompt"
    output_mode: Literal["json_only", "deliberation_then_json"] = "json_only"
    configuration: Mapping[str, Any] = Field(default_factory=dict)


class JudgeCriterionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    score: Decimal = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)


class JudgeDecision(BaseModel):
    """Narrow extension result; pass/fail remains core-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    score: Decimal = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)
    criteria: tuple[JudgeCriterionDecision, ...] = Field(min_length=1)
    metadata: Mapping[str, Any] = Field(default_factory=dict)


class JudgeProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class JudgeProviderResult(BaseModel):
    """Normalized result from core-owned provider execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    content: str
    usage: JudgeProviderUsage | None = None
    provider_request_id: str | None = Field(default=None, max_length=1000)
    finish_reason: str | None = Field(default=None, max_length=1000)
    cost: Decimal | None = Field(default=None, ge=0)


@runtime_checkable
class JudgeProvider(Protocol):
    """Narrow capability preserving endpoint security, limits, retries, and cancellation."""

    def complete(self, *, prompt: str, system_prompt: str | None = None) -> JudgeProviderResult: ...


@runtime_checkable
class JudgeImplementation(Protocol):
    """Synchronous API v1 implemented by trusted local Python code."""

    implementation_id: str
    implementation_version: str
    api_version: str
    configuration_model: type[BaseModel]

    def evaluate(
        self, request: JudgeEvaluationRequest, provider: JudgeProvider
    ) -> JudgeDecision: ...


class EmptyJudgeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JudgeImplementationRegistry:
    """Startup-only registry keyed by immutable implementation identity/version."""

    def __init__(self) -> None:
        self._implementations: dict[tuple[str, str], JudgeImplementation] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, implementation: JudgeImplementation) -> None:
        if self._frozen:
            raise JudgeRegistrationError("Judge registry is frozen.")
        identity = (implementation.implementation_id, implementation.implementation_version)
        if not _IMPLEMENTATION_NAME.fullmatch(identity[0]) or not _VERSION.fullmatch(identity[1]):
            raise JudgeRegistrationError("Judge implementation identity is invalid.")
        if implementation.api_version != JUDGE_EXTENSION_API_VERSION:
            raise JudgeRegistrationError("Judge implementation API version is incompatible.")
        if not isinstance(implementation.configuration_model, type) or not issubclass(
            implementation.configuration_model, BaseModel
        ):
            raise JudgeRegistrationError("Judge configuration model must be a Pydantic model.")
        if identity in self._implementations:
            raise JudgeRegistrationError("Judge implementation identity is already registered.")
        self._implementations[identity] = implementation

    def freeze(self) -> None:
        self._frozen = True

    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._implementations))

    def get(self, implementation_id: str, implementation_version: str) -> JudgeImplementation:
        try:
            return self._implementations[(implementation_id, implementation_version)]
        except KeyError as exc:
            raise JudgeImplementationUnavailable("Judge implementation is unavailable.") from exc

    def validate_configuration(
        self, implementation_id: str, implementation_version: str, value: Any
    ) -> dict[str, Any]:
        implementation = self.get(implementation_id, implementation_version)
        try:
            validated = implementation.configuration_model.model_validate(value)
        except ValidationError as exc:
            raise JudgeRegistrationError("Judge implementation configuration is invalid.") from exc
        payload = validated.model_dump(mode="json", round_trip=True)
        if not isinstance(payload, dict):
            raise JudgeRegistrationError("Judge implementation configuration must be an object.")
        if _contains_secret_field(payload):
            raise JudgeRegistrationError(
                "Judge implementation configuration must not contain credentials."
            )
        _bounded_json(payload, error_type=JudgeRegistrationError)
        return payload


RegistryHook = Callable[[JudgeImplementationRegistry], None]
_Model = TypeVar("_Model", bound=BaseModel)


def canonical_messages(prompt: str, system_prompt: str | None) -> tuple[JudgeMessage, ...]:
    """Deterministically convert current single-turn cases to canonical messages."""

    messages: list[JudgeMessage] = []
    if system_prompt is not None:
        messages.append(JudgeMessage(role="system", content=system_prompt))
    messages.append(JudgeMessage(role="user", content=prompt))
    return tuple(messages)


def validate_decision(
    decision: Any,
    request: JudgeEvaluationRequest,
    *,
    implementation_id: str,
    implementation_version: str,
) -> EvaluationResult:
    """Apply core-owned criterion, score, threshold, and metadata invariants."""

    try:
        validated = JudgeDecision.model_validate(decision)
    except ValidationError as exc:
        raise JudgeDecisionError("Judge decision is invalid.") from exc
    expected = [item.id for item in request.preset.rubric]
    actual = [item.id for item in validated.criteria]
    if len(actual) != len(set(actual)) or actual != expected:
        raise JudgeDecisionError("Judge decision criteria do not match preset.")
    by_id = {item.id: item for item in validated.criteria}
    weight_sum = sum((item.weight for item in request.preset.rubric), Decimal(0))
    if weight_sum <= 0:
        raise JudgeDecisionError("Judge preset has no positive criterion weight.")
    calculated = (
        sum((by_id[item.id].score * item.weight for item in request.preset.rubric), Decimal(0))
        / weight_sum
    )
    if abs(calculated - validated.score) > Decimal("0.01"):
        raise JudgeDecisionError("Judge decision score is inconsistent with criteria.")
    metadata = dict(validated.metadata)
    _bounded_json(metadata, error_type=JudgeDecisionError)
    result_metadata: dict[str, Any] = {
        "criteria": [
            {
                "id": item.id,
                "description": item.description,
                "score": str(by_id[item.id].score),
                "reason": by_id[item.id].reason,
                "weight": str(item.weight),
            }
            for item in request.preset.rubric
        ],
        "threshold": str(request.preset.threshold),
        "preset_id": request.preset.id,
        "preset_version": request.preset.version,
        "preset_name": request.preset.name,
        "preset_description": request.preset.description,
        "implementation_id": implementation_id,
        "implementation_version": implementation_version,
        "implementation_metadata": metadata,
        "expected_value": request.expected_result,
        "context": request.context,
        "actual": request.actual_result,
    }
    if implementation_id == BUILTIN_JUDGE_ID and implementation_version == BUILTIN_JUDGE_VERSION:
        # Preserve v0.1 built-in audit fields while all extensions also receive namespace.
        result_metadata.update(metadata)
    return EvaluationResult(
        method="llm_judge",
        score=calculated,
        passed=calculated >= request.preset.threshold,
        reason=validated.reason,
        metadata=result_metadata,
    )


def _contains_secret_field(value: Mapping[str, Any]) -> bool:
    forbidden = {"api_key", "apikey", "credential", "password", "secret", "token"}
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "_")
        if any(part in forbidden for part in normalized.split("_")):
            return True
        if isinstance(item, dict) and _contains_secret_field(item):
            return True
    return False


def _bounded_json(value: Mapping[str, Any], *, error_type: type[JudgeExtensionError]) -> None:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise error_type("Judge extension metadata is not JSON-serializable.") from exc
    if len(encoded) > MAX_EXTENSION_METADATA_BYTES:
        raise error_type("Judge extension metadata exceeds size limit.")
