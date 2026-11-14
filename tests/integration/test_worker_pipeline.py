from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from tests.integration.test_run_queue import FakeClock, fixture_app

from evaluator.run_exports import RunExportService
from evaluator.runs import RunQueue
from evaluator.workers import TargetOutcome, WorkerPipeline

pytestmark = pytest.mark.integration


def test_worker_completes_deterministic_run_and_partial_stream_errors(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "ok", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    worker = WorkerPipeline(
        queue,
        lambda case, model: TargetOutcome("x", metrics={"target_cost": "0.1"}),
        heartbeat_interval=0.01,
    )
    assert worker.run_once("worker")
    assert queue.get_run(run_id)["status"] == "completed"
    second = queue.create_run(
        {"name": "partial", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    partial = WorkerPipeline(
        queue,
        lambda case, model: TargetOutcome("partial", evaluate=False, error_code="unexpected_eof"),
        heartbeat_interval=0.01,
    )
    assert partial.run_once("worker")
    assert queue.get_run(second)["status"] == "failed"


def test_guardrail_persists_partial_content_metrics_and_safe_export(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "guarded", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    worker = WorkerPipeline(
        queue,
        lambda case, model: TargetOutcome(
            "partial candidate",
            evaluate=False,
            metrics={"target_latency_ms": "12", "input_tokens": 4},
            error_code="provider_guardrail",
            outcome_category="provider_guardrail",
            provider_evidence={"finish_reason": "content_filter"},
        ),
        heartbeat_interval=0.01,
    )
    assert worker.run_once("worker")

    data = RunExportService(app.extensions["database_factory"]).data(run_id)
    result = data["results"][0]
    assert result["status"] == "error"
    assert result["outcome_category"] == "provider_guardrail"
    assert result["outcome_stage"] == "target"
    assert result["target_latency_ms"] == "12" and result["input_tokens"] == 4
    assert result["provider_evidence_json"] == '{"finish_reason":"content_filter"}'
    assert "partial candidate" in result["result_json"]
    assert queue.get_run(run_id)["status"] == "failed"


def test_interrupted_provider_job_remains_recoverable(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    clock = FakeClock()
    queue = RunQueue(app.extensions["database_factory"], clock=clock, lease_seconds=1)
    run_id = queue.create_run(
        {
            "name": "interrupted",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )
    cancelled = threading.Event()

    def interrupt(case, model):
        cancelled.set()
        return TargetOutcome("ignored")

    worker = WorkerPipeline(queue, interrupt, heartbeat_interval=0.01)

    assert worker.run_once("worker", cancelled=cancelled)
    assert queue.get_run(run_id)["status"] == "running"
    clock.now += timedelta(seconds=2)
    assert queue.recover_expired() == {"jobs": 1, "runs": 1}
    assert queue.claim("recovery-worker") is not None


def test_cancel_skips_pending_jobs(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "cancel", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    queue.cancel(run_id)
    assert queue.get_run(run_id)["status"] == "cancelled" and queue.claim("worker") is None
