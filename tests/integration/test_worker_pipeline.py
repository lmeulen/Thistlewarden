from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_run_queue import fixture_app

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


def test_cancel_skips_pending_jobs(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "cancel", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    queue.cancel(run_id)
    assert queue.get_run(run_id)["status"] == "cancelled" and queue.claim("worker") is None
