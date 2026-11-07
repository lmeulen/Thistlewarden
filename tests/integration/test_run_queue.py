from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.runs import LeaseLost, RunQueue

pytestmark = pytest.mark.integration


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def now_utc(self):
        return self.now

    def monotonic(self):
        return 0.0


def fixture_app(tmp_path: Path):
    app = create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "runs.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "P", "completions_url": "https://p.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id,
        {
            "model_id": "m",
            "display_name": "M",
            "pricing": {"input_per_million": "1.23456789", "output_per_million": "2"},
        },
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "G"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "c",
            "prompt": "secret prompt",
            "expected_answer": "x",
            "evaluation_method": "exact_match",
        },
    )
    return app, golden, model


def test_judge_only_model_is_excluded_from_target_aggregates(tmp_path: Path) -> None:
    app, golden, target = fixture_app(tmp_path)
    endpoint = app.extensions["endpoint_service"].list_all()[0]
    judge_model = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "judge", "display_name": "Judge"}
    )
    judge = app.extensions["judge_profile_service"].create(
        {"name": "J", "model_profile_id": str(judge_model.id)}
    )
    app.extensions["judge_profile_service"].set_golden_set_override(golden.id, judge.id)
    run_id = app.extensions["run_queue"].create_run(
        {
            "name": "R",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(target.id)],
        }
    )

    aggregates = app.extensions["aggregate_service"].for_run(run_id)
    models = app.extensions["run_view_service"].models(run_id)
    assert len(aggregates) == 1
    assert len(models) == 1
    assert models[0]["display_name"] == "M"


def test_atomic_snapshots_jobs_and_fenced_expiry(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    clock = FakeClock()
    queue = RunQueue(app.extensions["database_factory"], clock=clock, lease_seconds=10)
    run_id = queue.create_run(
        {"name": "R", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    run = queue.get_run(run_id)
    assert run["job_counts"] == {"pending": 1}
    first = queue.claim("one")
    assert first is not None and first.generation == 1
    case = queue.snapshot(first.case_snapshot_id, "case")
    profile = queue.snapshot(first.model_snapshot_id, "model")
    assert case["prompt"] == "secret prompt" and profile["input_per_million"] == "1.23456789"
    assert "api_key_secret_id" not in profile
    clock.now += timedelta(seconds=11)
    second = queue.claim("two")
    assert second is not None and second.generation == 2
    with pytest.raises(LeaseLost):
        queue.record_attempt(first, None, {})
    queue.record_attempt(second, None, {"safe": "value"})
    queue.complete(second, status="passed", score="1", weight="1", result={"passed": True})
    assert queue.claim("three") is None
