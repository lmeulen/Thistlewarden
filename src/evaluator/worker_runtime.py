"""Bounded production worker threads and streaming target adapter."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from evaluator.costs import PricingSnapshot, TokenUsage, calculate_cost
from evaluator.endpoints import SQLiteEndpointRepository
from evaluator.judging.execution import JudgeCaller
from evaluator.logging_config import log_event, log_unexpected_exception
from evaluator.models import ModelProfileInput, ModelProfileRecord, PricingInput
from evaluator.provider import ConversationMessage
from evaluator.rate_limits import ProviderCallExecutor
from evaluator.streaming import OpenAIStreamingClient, StreamingCompletionResult
from evaluator.workers import TargetOutcome, WorkerPipeline

_LOG = logging.getLogger(__name__)

__all__ = [
    "JudgeCaller",
    "ModelProfileTester",
    "ModelTestResult",
    "StreamingTargetCaller",
    "WorkerRuntime",
]


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


@dataclass(frozen=True, slots=True)
class ModelTestResult:
    content: str
    successful: bool
    error_code: str | None
    finish_reason: str | None
    total_latency_ms: Decimal
    ttft_ms: Decimal | None
    tokens_per_second: Decimal | None
    input_tokens: int | None
    output_tokens: int | None


class ModelProfileTester:
    """Execute safe interactive test calls using persisted model specifications."""

    TEST_PROMPT = "Reply with exactly MODEL_TEST_OK."
    TEST_SYSTEM_PROMPT = "Follow the instruction exactly and add no other text."

    def __init__(
        self,
        endpoints: SQLiteEndpointRepository,
        client: OpenAIStreamingClient,
        executor: ProviderCallExecutor,
    ) -> None:
        self._endpoints = endpoints
        self._client = client
        self._executor = executor

    def test(self, profile: ModelProfileRecord) -> ModelTestResult:
        endpoint = self._endpoints.get(profile.endpoint_id)
        if endpoint is None or not endpoint.data.active:
            raise ValueError("endpoint unavailable")
        result = self._executor.execute(
            endpoint,
            lambda: self._client.complete(
                endpoint,
                profile.data,
                prompt=self.TEST_PROMPT,
                system_prompt=self.TEST_SYSTEM_PROMPT,
                diagnostic_mode=True,
            ),
            estimated_tokens=profile.data.max_tokens or 0,
            usage_tokens=lambda item: None if item.usage is None else item.usage.total_tokens,
        )
        usage = result.usage
        log_event(
            _LOG,
            logging.INFO,
            "model_connection_tested",
            endpoint_id=endpoint.id,
            model_id=profile.data.model_id,
            outcome="success" if result.successful else result.error_code,
            duration_ms=result.total_latency_ms,
            usage_present=usage is not None,
        )
        return ModelTestResult(
            content=result.content,
            successful=result.successful,
            error_code=result.error_code,
            finish_reason=result.finish_reason,
            total_latency_ms=result.total_latency_ms,
            ttft_ms=result.ttft_ms,
            tokens_per_second=result.tokens_per_second,
            input_tokens=None if usage is None else usage.prompt_tokens,
            output_tokens=None if usage is None else usage.completion_tokens,
        )


class StreamingTargetCaller:
    def __init__(
        self,
        endpoints: SQLiteEndpointRepository,
        client: OpenAIStreamingClient,
        executor: ProviderCallExecutor,
        cancelled: threading.Event | None = None,
    ) -> None:
        self._endpoints = endpoints
        self._client = client
        self._executor = executor
        self._cancelled = cancelled

    def __call__(self, case: dict[str, Any], model: dict[str, Any]) -> TargetOutcome:
        endpoint = self._endpoints.get(UUID(model["endpoint_id"]))
        if endpoint is None or not endpoint.data.active:
            return TargetOutcome("", evaluate=False, error_code="endpoint_unavailable")
        profile = _profile_from_snapshot(model)
        result = self._executor.execute(
            endpoint,
            lambda: self._client.complete(
                endpoint,
                profile,
                prompt=(None if case.get("conversation_messages") is not None else case["prompt"]),
                messages=(
                    None
                    if case.get("conversation_messages") is None
                    else [
                        ConversationMessage.model_validate(message)
                        for message in case["conversation_messages"]
                    ]
                ),
                system_prompt=case.get("system_prompt"),
            ),
            estimated_tokens=profile.max_tokens or 0,
            usage_tokens=self._usage_tokens,
            cancelled=self._cancelled,
        )
        if self._cancelled is not None and self._cancelled.is_set():
            raise CancelledError
        usage = result.usage
        log_event(
            _LOG,
            logging.DEBUG,
            "target_stream_completed",
            endpoint_id=endpoint.id,
            model_id=model["model_id"],
            duration_ms=result.total_latency_ms,
            ttft_ms=result.ttft_ms,
            response_bytes=len(result.content.encode()),
            finish_reason=result.finish_reason,
            usage_present=usage is not None,
            outcome="success" if result.successful else result.error_code,
        )
        metrics: dict[str, Any] = {
            "target_latency_ms": str(result.total_latency_ms),
            "ttft_ms": None if result.ttft_ms is None else str(result.ttft_ms),
            "tps": None if result.tokens_per_second is None else str(result.tokens_per_second),
        }
        cached = (
            None if usage is None else _detail_token(usage.prompt_tokens_details, "cached_tokens")
        )
        reasoning = (
            None
            if usage is None
            else _detail_token(usage.completion_tokens_details, "reasoning_tokens")
        )
        metrics.update(
            input_tokens=None if usage is None else usage.prompt_tokens,
            output_tokens=None if usage is None else usage.completion_tokens,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
        )
        calculation = calculate_cost(
            model_profile_id=UUID(model["id"]),
            usage=TokenUsage(
                input_tokens=0 if usage is None else usage.prompt_tokens,
                output_tokens=0 if usage is None else usage.completion_tokens,
                cached_input_tokens=cached or 0,
                reasoning_tokens=reasoning or 0,
            ),
            pricing=PricingSnapshot.from_pricing(profile.pricing),
            chargeable=True,
        )
        metrics["target_cost"] = (
            None if calculation.total_cost is None else str(calculation.total_cost)
        )
        return TargetOutcome(
            result.content,
            evaluate=result.evaluate,
            metrics=metrics,
            attempts=(
                {
                    "category": "target",
                    "provider_request_id": result.provider_request_id,
                    "outcome_category": result.outcome_category,
                },
            ),
            error_code=result.error_code,
            outcome_category=result.outcome_category,
            provider_evidence=result.provider_evidence.as_dict(),
        )

    @staticmethod
    def _usage_tokens(result: StreamingCompletionResult) -> int | None:
        return None if result.usage is None else result.usage.total_tokens


class WorkerRuntime:
    """Own fixed worker set; idle polling stops promptly during shutdown."""

    def __init__(
        self,
        pipeline_factory: Callable[[], WorkerPipeline],
        *,
        concurrency: int,
        idle_seconds: float = 0.25,
        cancel_in_flight: Callable[[], None] | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        self._factory = pipeline_factory
        self._concurrency = concurrency
        self._idle_seconds = idle_seconds
        self._cancel_in_flight = cancel_in_flight
        self._stop = cancellation_event or threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def running_threads(self) -> int:
        return sum(thread.is_alive() for thread in self._threads)

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("worker runtime already started")
        self._stop.clear()
        for index in range(self._concurrency):
            owner = f"{uuid4()}:{index}"
            thread = threading.Thread(
                target=self._loop,
                args=(owner,),
                name=f"evaluator-worker-{index}",
                # Timed-out provider transports must never keep process alive past grace.
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
        log_event(_LOG, logging.INFO, "worker_runtime_started", concurrency=self._concurrency)

    def stop(self, timeout: float = 30.0) -> bool:
        self._stop.set()
        if self._cancel_in_flight is not None:
            self._cancel_in_flight()
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        stopped = not any(thread.is_alive() for thread in self._threads)
        if stopped:
            self._threads.clear()
        log_event(_LOG, logging.INFO, "worker_runtime_stopped", stopped=stopped)
        return stopped

    def _loop(self, owner: str) -> None:
        pipeline = self._factory()
        while not self._stop.is_set():
            try:
                worked = pipeline.run_once(owner, cancelled=self._stop)
            except Exception as exc:
                log_unexpected_exception(_LOG, "worker_iteration_failed", exc)
                worked = False
            if not worked:
                self._stop.wait(self._idle_seconds)


def _detail_token(details: dict[str, Any] | None, name: str) -> int | None:
    value = None if details is None else details.get(name)
    return value if isinstance(value, int) and value >= 0 else None
