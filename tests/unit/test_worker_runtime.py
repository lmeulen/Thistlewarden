from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.models import ModelProfileInput, ModelProfileRecord
from evaluator.worker_runtime import ModelProfileTester, WorkerRuntime, _apply_judge_overrides


class ImmediateExecutor:
    def execute(self, endpoint, call, **kwargs):
        return call()


class FakeStreamingClient:
    def __init__(self) -> None:
        self.call = None

    def complete(self, endpoint, profile, **kwargs):
        self.call = (endpoint, profile, kwargs)
        return SimpleNamespace(
            content="MODEL_TEST_OK",
            successful=True,
            error_code=None,
            finish_reason="stop",
            total_latency_ms=Decimal("10"),
            ttft_ms=Decimal("2"),
            tokens_per_second=Decimal("5"),
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )


class EndpointRepository:
    def __init__(self, endpoint) -> None:
        self.endpoint = endpoint

    def get(self, endpoint_id):
        return self.endpoint if endpoint_id == self.endpoint.id else None


class Pipeline:
    def __init__(self, calls: list[str], block: threading.Event | None = None) -> None:
        self.calls = calls
        self.block = block

    def run_once(self, owner: str, *, cancelled: threading.Event | None = None) -> bool:
        self.calls.append(owner)
        if self.block is not None:
            self.block.wait(2)
        return False


def test_model_tester_uses_complete_persisted_specification() -> None:
    now = datetime.now(UTC)
    endpoint = EndpointRecord(
        uuid4(),
        EndpointInput(name="Provider", completions_url="https://provider.example/chat/completions"),
        None,
        now,
        now,
    )
    profile = ModelProfileRecord(
        uuid4(),
        endpoint.id,
        ModelProfileInput(
            model_id="configured-model",
            display_name="Configured",
            temperature=Decimal("0.4"),
            top_p=Decimal("0.9"),
            max_tokens=123,
            reasoning_field_path="reasoning.effort",
            reasoning_value="high",
            custom_parameters={"seed": 7},
        ),
        now,
        now,
    )
    client = FakeStreamingClient()
    tester = ModelProfileTester(
        EndpointRepository(endpoint),
        client,
        ImmediateExecutor(),  # type: ignore[arg-type]
    )

    result = tester.test(profile)

    assert result.successful and result.content == "MODEL_TEST_OK"
    assert client.call is not None
    called_endpoint, called_profile, kwargs = client.call
    assert called_endpoint is endpoint
    assert called_profile is profile.data
    assert called_profile.custom_parameters == {"seed": 7}
    assert kwargs == {
        "prompt": "Reply with exactly MODEL_TEST_OK.",
        "system_prompt": "Follow the instruction exactly and add no other text.",
        "diagnostic_mode": True,
    }


def test_judge_reasoning_override_replaces_temperature() -> None:
    profile = ModelProfileInput(
        model_id="reasoner",
        display_name="Reasoner",
        temperature=Decimal("0.4"),
        reasoning_field_path="reasoning.effort",
        reasoning_value="low",
        max_tokens=100,
    )

    overridden = _apply_judge_overrides(
        profile,
        {
            "temperature_enabled": 0,
            "reasoning_value": "high",
            "max_output_tokens": 250,
        },
    )

    assert overridden.temperature is None
    assert overridden.reasoning_value == "high"
    assert overridden.max_tokens == 250


def test_judge_reasoning_override_requires_model_binding() -> None:
    profile = ModelProfileInput(model_id="m", display_name="M")

    with pytest.raises(RuntimeError, match="no reasoning field path"):
        _apply_judge_overrides(
            profile,
            {"temperature_enabled": 0, "reasoning_value": "high"},
        )


def test_worker_runtime_is_bounded_and_stops_idle_threads() -> None:
    calls: list[str] = []
    runtime = WorkerRuntime(lambda: Pipeline(calls), concurrency=3, idle_seconds=0.01)  # type: ignore[arg-type]
    runtime.start()
    deadline = time.monotonic() + 1
    while len(set(calls)) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.running_threads == 3
    assert len(set(calls)) == 3
    assert runtime.stop(timeout=1)
    assert runtime.running_threads == 0


def test_multiple_blocked_workers_share_one_global_shutdown_deadline() -> None:
    release = threading.Event()
    runtime = WorkerRuntime(lambda: Pipeline([], release), concurrency=3)  # type: ignore[arg-type]
    runtime.start()

    started = time.monotonic()
    assert not runtime.stop(timeout=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 0.12
    assert runtime.running_threads == 3
    release.set()
    assert runtime.stop(timeout=1)


def test_stop_closes_in_flight_transport_and_clears_worker_state() -> None:
    release = threading.Event()
    callback_calls: list[bool] = []

    def cancel_in_flight() -> None:
        callback_calls.append(True)
        release.set()

    runtime = WorkerRuntime(
        lambda: Pipeline([], release),  # type: ignore[arg-type]
        concurrency=2,
        cancel_in_flight=cancel_in_flight,
    )
    runtime.start()

    assert runtime.stop(timeout=1)
    assert callback_calls == [True]
    assert runtime.running_threads == 0


def test_worker_runtime_reports_shutdown_timeout_then_recovers() -> None:
    release = threading.Event()
    runtime = WorkerRuntime(lambda: Pipeline([], release), concurrency=1)  # type: ignore[arg-type]
    runtime.start()
    assert not runtime.stop(timeout=0.01)
    release.set()
    assert runtime.stop(timeout=1)
