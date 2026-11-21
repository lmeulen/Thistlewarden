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


def test_multi_turn_worker_scores_final_response_and_checkpoints_transcript(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    service = app.extensions["golden_set_service"]
    items, _ = service.list_items(
        golden.id, page=1, per_page=10, category=None, tag=None, enabled=None
    )
    item = items[0]
    service.update_item(item.id, {"user_prompts": ["first", "final"]})
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "multi", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    calls: list[list[dict[str, str]]] = []

    def target(case, profile):
        del profile
        calls.append(case["conversation_messages"])
        return TargetOutcome(
            "intermediate" if len(calls) == 1 else "x",
            metrics={
                "target_cost": "0.1",
                "target_latency_ms": "10",
                "input_tokens": 2,
                "output_tokens": 3,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
            },
        )

    assert WorkerPipeline(queue, target, heartbeat_interval=0.01).run_once("worker")
    assert calls == [
        [{"role": "user", "content": "first"}],
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "intermediate"},
            {"role": "user", "content": "final"},
        ],
    ]
    result = app.extensions["run_export_service"].data(run_id)["results"][0]
    payload = __import__("json").loads(result["result_json"])
    assert payload["candidate"] == payload["evaluated_answer"] == "x"
    assert payload["transcript"][-1] == {"role": "assistant", "content": "x"}
    assert result["target_cost"] == "0.2"
    assert result["input_tokens"] == 4 and result["output_tokens"] == 6
    connection = app.extensions["database_factory"].connect()
    try:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT status,assistant_response FROM run_job_turns ORDER BY turn_index"
            ).fetchall()
        ] == [("completed", "intermediate"), ("completed", "x")]
    finally:
        connection.close()


def test_recovery_does_not_replay_incomplete_target_turn(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {
            "name": "interrupted-turn",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )
    lease = queue.claim("first")
    assert lease is not None
    queue.start_turn(lease, 0, "prompt")
    connection = app.extensions["database_factory"].connect()
    try:
        connection.execute("UPDATE run_jobs SET lease_expires_at='1970-01-01T00:00:00+00:00'")
        connection.commit()
    finally:
        connection.close()
    calls: list[bool] = []
    assert WorkerPipeline(
        queue,
        lambda case, profile: (calls.append(True), TargetOutcome("never"))[1],
        heartbeat_interval=0.01,
    ).run_once("second")
    assert calls == []
    assert queue.get_run(run_id)["status"] == "failed"
    export = app.extensions["run_export_service"].data(run_id)
    assert __import__("json").loads(export["results"][0]["result_json"])["outcome_code"] == (
        "incomplete_target_turn"
    )


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


def test_cancel_during_target_terminalizes_claimed_job(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = app.extensions["run_queue"]
    run_id = queue.create_run(
        {"name": "cancel", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )

    def cancel_during_target(case, profile):
        del case, profile
        queue.cancel(run_id)
        return TargetOutcome("too late")

    worker = WorkerPipeline(queue, cancel_during_target, heartbeat_interval=0.01)
    assert worker.run_once("worker")
    exported = app.extensions["run_export_service"].data(run_id)
    assert exported["run"]["status"] == "cancelled"
    assert [job["status"] for job in exported["jobs"]] == ["cancelled"]
    assert exported["results"] == []


def test_unexpected_pipeline_defect_persists_generic_result(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "defect", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    sensitive = "prompt=never-log answer=never-log api-key=never-log"

    def defect(case, profile):
        del case, profile
        raise RuntimeError(sensitive)

    assert WorkerPipeline(queue, defect, heartbeat_interval=0.01).run_once("worker")

    result = app.extensions["run_export_service"].data(run_id)["results"][0]
    assert result["status"] == "error"
    assert sensitive not in result["result_json"]
    assert "RuntimeError" in result["result_json"]


def test_cancel_skips_pending_jobs(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = RunQueue(app.extensions["database_factory"])
    run_id = queue.create_run(
        {"name": "cancel", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    queue.cancel(run_id)
    assert queue.get_run(run_id)["status"] == "cancelled" and queue.claim("worker") is None
