"""Non-streaming judge provider invocation and result validation."""

from __future__ import annotations

import json
import threading
from concurrent.futures import CancelledError
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from evaluator.costs import PricingSnapshot, TokenUsage, calculate_cost
from evaluator.endpoints import SQLiteEndpointRepository
from evaluator.evaluators.contracts import LlmJudgeConfig
from evaluator.evaluators.core import EvaluationResult
from evaluator.judging.builtin import register_builtin_judge
from evaluator.judging.extensions import (
    JudgeEvaluationRequest,
    JudgeExecutionError,
    JudgeExtensionError,
    JudgeImplementationRegistry,
    JudgePresetSnapshot,
    JudgeProviderResult,
    JudgeProviderUsage,
    canonical_messages,
    validate_decision,
)
from evaluator.judging.presets import get_judge_preset
from evaluator.models import ModelProfileInput, PricingInput
from evaluator.provider import OpenAIProviderClient, ProviderGuardrailError
from evaluator.rate_limits import ProviderCallExecutor
from evaluator.runs import RunQueue


def _profile_from_snapshot(model: dict[str, Any]) -> ModelProfileInput:
    decimal_fields = (
        "input_per_million",
        "output_per_million",
        "cached_input_per_million",
        "reasoning_per_million",
        "fixed_per_request",
        "high_context_input_per_million",
        "high_context_output_per_million",
        "high_context_cached_input_per_million",
        "high_context_reasoning_per_million",
    )
    pricing_values = {
        key: None if model.get(key) is None else Decimal(str(model[key])) for key in decimal_fields
    }
    pricing_values["context_threshold"] = model.get("context_threshold")
    return ModelProfileInput(
        model_id=model["model_id"],
        display_name=model["display_name"],
        description=model.get("description"),
        temperature=None
        if model.get("temperature") is None
        else Decimal(str(model["temperature"])),
        top_p=None if model.get("top_p") is None else Decimal(str(model["top_p"])),
        max_tokens=model.get("max_tokens"),
        max_tokens_field_path=model.get("max_tokens_field_path", "max_tokens"),
        reasoning_field_path=model.get("reasoning_field_path"),
        reasoning_value=model.get("reasoning_value"),
        send_reasoning_off=bool(model.get("send_reasoning_off")),
        custom_parameters=json.loads(model.get("custom_parameters_json", "{}")),
        pricing=PricingInput.model_validate(pricing_values),
        active=bool(model.get("active", True)),
    )


def _apply_judge_overrides(
    profile: ModelProfileInput, snapshot: dict[str, Any]
) -> ModelProfileInput:
    updates: dict[str, Any] = {}
    if snapshot.get("temperature_enabled"):
        updates["temperature"] = Decimal(str(snapshot["temperature"]))
        updates["reasoning_value"] = None
    elif snapshot.get("reasoning_value") is not None:
        if profile.reasoning_field_path is None:
            raise RuntimeError("judge model has no reasoning field path configured")
        updates["temperature"] = None
        updates["reasoning_value"] = snapshot["reasoning_value"]
    if snapshot.get("max_output_tokens") is not None:
        updates["max_tokens"] = snapshot["max_output_tokens"]
    return profile.model_copy(update=updates)


class _CoreJudgeProvider:
    """Per-evaluation provider capability; never exposes endpoint credentials."""

    def __init__(
        self,
        endpoint: Any,
        profile: ModelProfileInput,
        client: OpenAIProviderClient,
        executor: ProviderCallExecutor,
        cancelled: threading.Event | None,
    ) -> None:
        self._endpoint = endpoint
        self._profile = profile
        self._client = client
        self._executor = executor
        self._cancelled = cancelled
        self._calls: list[JudgeProviderResult] = []

    @property
    def calls(self) -> tuple[JudgeProviderResult, ...]:
        return tuple(self._calls)

    def complete(self, *, prompt: str, system_prompt: str | None = None) -> JudgeProviderResult:
        if len(self._calls) >= 2:
            raise RuntimeError("judge implementation exceeded provider-call limit")
        completion = self._executor.execute(
            self._endpoint,
            lambda: self._client.complete(
                self._endpoint, self._profile, prompt=prompt, system_prompt=system_prompt
            ),
            estimated_tokens=self._profile.max_tokens or 0,
            usage_tokens=lambda item: None if item.usage is None else item.usage.total_tokens,
            cancelled=self._cancelled,
        )
        if self._cancelled is not None and self._cancelled.is_set():
            raise CancelledError
        if not completion.evaluate:
            raise ProviderGuardrailError(completion.provider_evidence)
        usage = completion.usage
        normalized_usage = (
            None
            if usage is None
            else TokenUsage(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cached_input_tokens=(
                    _detail_token(usage.prompt_tokens_details, "cached_tokens") or 0
                ),
                reasoning_tokens=(
                    _detail_token(usage.completion_tokens_details, "reasoning_tokens") or 0
                ),
            )
        )
        calculation = calculate_cost(
            model_profile_id=UUID(int=0),
            usage=normalized_usage
            or TokenUsage(
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                reasoning_tokens=0,
            ),
            pricing=PricingSnapshot.from_pricing(self._profile.pricing),
            chargeable=True,
        )
        result = JudgeProviderResult(
            content=completion.content,
            usage=None
            if usage is None
            else JudgeProviderUsage(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                cached_input_tokens=_detail_token(usage.prompt_tokens_details, "cached_tokens"),
                reasoning_tokens=_detail_token(usage.completion_tokens_details, "reasoning_tokens"),
            ),
            provider_request_id=completion.provider_request_id,
            finish_reason=completion.finish_reason,
            cost=calculation.total_cost,
        )
        self._calls.append(result)
        return result


class JudgeCaller:
    def __init__(
        self,
        queue: RunQueue,
        endpoints: SQLiteEndpointRepository,
        client: OpenAIProviderClient,
        executor: ProviderCallExecutor,
        cancelled: threading.Event | None = None,
        registry: JudgeImplementationRegistry | None = None,
    ) -> None:
        self._queue = queue
        self._endpoints = endpoints
        self._client = client
        self._executor = executor
        self._cancelled = cancelled
        if registry is None:
            registry = JudgeImplementationRegistry()
            register_builtin_judge(registry)
            registry.freeze()
        self._registry = registry

    def __call__(self, run_id: UUID, case: dict[str, Any], candidate: str) -> EvaluationResult:
        snapshot = self._queue.judge_snapshot(run_id)
        if snapshot is None:
            raise RuntimeError("judge snapshot unavailable")
        implementation_id = str(snapshot["implementation_id"])
        implementation_version = str(snapshot["implementation_version"])
        implementation = self._registry.get(implementation_id, implementation_version)
        model = self._queue.model_snapshot_for_source(run_id, UUID(snapshot["model_profile_id"]))
        endpoint = self._endpoints.get(UUID(model["endpoint_id"]))
        if endpoint is None or not endpoint.data.active:
            raise RuntimeError("judge endpoint unavailable")
        profile = _apply_judge_overrides(_profile_from_snapshot(model), snapshot)
        config = LlmJudgeConfig.model_validate(json.loads(case["evaluator_config_json"]))
        preset = get_judge_preset(config.preset_id, config.preset_version)
        configuration = self._registry.validate_configuration(
            implementation_id,
            implementation_version,
            snapshot.get("implementation_config", {}),
        )
        request = JudgeEvaluationRequest(
            messages=canonical_messages(case["prompt"], case.get("system_prompt")),
            expected_result=config.expected_value,
            actual_result=candidate,
            context=config.context,
            preset=JudgePresetSnapshot.from_preset(preset),
            configuration=configuration,
        )
        provider = _CoreJudgeProvider(
            endpoint, profile, self._client, self._executor, self._cancelled
        )
        try:
            decision = implementation.evaluate(request, provider)
        except (CancelledError, JudgeExtensionError):
            raise
        except Exception as exc:
            raise JudgeExecutionError("Judge implementation failed.") from exc
        result = validate_decision(
            decision,
            request,
            implementation_id=implementation_id,
            implementation_version=implementation_version,
        )
        total_cost = sum((call.cost or Decimal(0) for call in provider.calls), Decimal(0))
        return cast(
            EvaluationResult,
            result.model_copy(
                update={
                    "metadata": {
                        **result.metadata,
                        "provider_calls": [
                            {
                                "usage": None
                                if call.usage is None
                                else call.usage.model_dump(mode="json"),
                                "provider_request_id": call.provider_request_id,
                                "finish_reason": call.finish_reason,
                                "cost": None if call.cost is None else str(call.cost),
                            }
                            for call in provider.calls
                        ],
                        "judge_cost": str(total_cost),
                    }
                }
            ),
        )


def _detail_token(details: dict[str, Any] | None, name: str) -> int | None:
    value = None if details is None else details.get(name)
    return value if isinstance(value, int) and value >= 0 else None
