from __future__ import annotations

import base64
import json
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from evaluator import create_app
from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.endpoints import SQLiteEndpointRepository
from evaluator.provider import CompletionResult, ProviderUsage
from evaluator.runs import LeaseLost, RunQueue
from evaluator.worker_runtime import JudgeCaller
from evaluator.workers import TargetOutcome, WorkerPipeline

pytestmark = pytest.mark.integration


class FakeJudgeClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, endpoint, profile, *, prompt: str, system_prompt=None) -> CompletionResult:
        del endpoint, profile, system_prompt
        self.prompts.append(prompt)
        data_text = prompt.split("<evaluation-data>\n", 1)[1]
        data = json.loads(data_text.split("\n</evaluation-data>", 1)[0])
        criteria = [
            {"id": item["id"], "score": "1", "reason": "Correct."} for item in data["rubric"]
        ]
        return CompletionResult(
            content=json.dumps(
                {"score": "1", "passed": True, "reason": "Pass.", "criteria": criteria}
            ),
            usage=ProviderUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            provider_request_id="fake-judge",
            finish_reason="stop",
        )


class FailingLaterMetricJudgeClient(FakeJudgeClient):
    def complete(self, endpoint, profile, *, prompt: str, system_prompt=None) -> CompletionResult:
        if len(self.prompts) >= 1:
            del endpoint, profile, system_prompt
            self.prompts.append(prompt)
            return CompletionResult(
                content="not-json",
                usage=ProviderUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                provider_request_id=f"failed-parse-{len(self.prompts)}",
                finish_reason="stop",
            )
        return super().complete(endpoint, profile, prompt=prompt, system_prompt=system_prompt)


class CancellingJudgeClient(FakeJudgeClient):
    def __init__(self, cancelled: threading.Event) -> None:
        super().__init__()
        self.cancelled = cancelled

    def complete(self, endpoint, profile, *, prompt: str, system_prompt=None) -> CompletionResult:
        result = super().complete(endpoint, profile, prompt=prompt, system_prompt=system_prompt)
        self.cancelled.set()
        return result


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
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
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


def test_run_rename_changes_only_mutable_name(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    queue = app.extensions["run_queue"]
    run_id = queue.create_run(
        {
            "name": "Original",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )
    before = app.extensions["run_view_service"].run(run_id)

    queue.rename(run_id, "  Revised  ")

    after = app.extensions["run_view_service"].run(run_id)
    assert after["name"] == "Revised"
    for key in ("id", "status", "total_jobs", "created_at"):
        assert after[key] == before[key]


def test_run_rename_rejects_invalid_or_unknown_run(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    queue = app.extensions["run_queue"]

    for name in ("", "   ", "x" * 201):
        with pytest.raises(ValidationApiError):
            queue.rename(uuid4(), name)
    with pytest.raises(NotFoundError):
        queue.rename(uuid4(), "Valid name")


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
    judge_snapshot = app.extensions["run_queue"].judge_snapshot(run_id)
    assert judge_snapshot is not None
    assert (
        not {
            "prompt_template",
            "template_version",
            "threshold",
            "rubric_json",
        }
        & judge_snapshot.keys()
    )
    assert len(aggregates) == 1
    assert len(models) == 1
    assert models[0]["display_name"] == "M"


def test_run_snapshots_published_judge_definition_semantics(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["judge_profile_service"].create(
        {"name": "Judge model", "model_profile_id": str(model.id), "is_global_default": True}
    )
    definitions = app.extensions["judge_definition_service"]
    definition = definitions.create(
        {
            "name": "Snapshot judge",
            "mode": "single_prompt",
            "threshold": "0.75",
            "general_instructions": "Assess factual accuracy.",
            "metrics": [{"metric_id": "correctness", "weight": "3"}],
        }
    )
    published = definitions.publish(definition.id)
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "versioned-judge",
            "prompt": "What is 2 + 2?",
            "evaluation_method": "llm_judge",
            "evaluator_config": {
                "judge_definition_id": definition.id,
                "judge_definition_version": "1.0",
            },
        },
    )

    run_id = app.extensions["run_queue"].create_run(
        {"name": "Versioned", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    connection = app.extensions["database_factory"].connect()
    try:
        row = connection.execute(
            """SELECT snapshot_json FROM run_case_snapshots
            WHERE run_id=? AND case_id='versioned-judge'""",
            (str(run_id),),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    snapshot = json.loads(row["snapshot_json"])
    assert snapshot["evaluator_config_json"] == json.dumps(
        {
            "judge_definition_id": definition.id,
            "judge_definition_version": "1.0",
        },
        separators=(",", ":"),
    )
    assert snapshot["judge_definition"] == {
        "id": definition.id,
        "version": "1.0",
        "semantic_hash": published.semantic_hash,
        "data": published.data.model_dump(mode="json"),
    }


def test_run_rejects_llm_judge_cases_without_active_judge_profile(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "judge-without-profile",
            "prompt": "Question",
            "evaluation_method": "llm_judge",
            "evaluator_config": {"preset_id": "general-quality", "preset_version": "1.0"},
        },
    )

    with pytest.raises(ValidationApiError, match="require an active Judge profile"):
        app.extensions["run_queue"].create_run(
            {
                "name": "Missing judge profile",
                "golden_set_id": str(golden.id),
                "model_profile_ids": [str(model.id)],
            }
        )
    connection = app.extensions["database_factory"].connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM run_jobs").fetchone()[0] == 0
    finally:
        connection.close()


def test_published_judge_snapshot_executes_single_and_per_metric_modes(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["judge_profile_service"].create(
        {"name": "Judge model", "model_profile_id": str(model.id), "is_global_default": True}
    )
    definitions = app.extensions["judge_definition_service"]
    for mode, metrics in (
        (
            "single_prompt",
            [{"metric_id": "correctness", "weight": "1"}],
        ),
        (
            "per_metric",
            [
                {"metric_id": "correctness", "weight": "1", "instructions": "Check facts."},
                {"metric_id": "relevance", "weight": "1", "instructions": "Check relevance."},
            ],
        ),
    ):
        definition = definitions.create(
            {
                "name": mode,
                "mode": mode,
                "threshold": "0.5",
                "general_instructions": "Assess answer." if mode == "single_prompt" else None,
                "metrics": metrics,
            }
        )
        definitions.publish(definition.id)
        app.extensions["golden_set_service"].create_item(
            golden.id,
            {
                "case_id": mode,
                "prompt": "Question",
                "evaluation_method": "llm_judge",
                "evaluator_config": {
                    "judge_definition_id": definition.id,
                    "judge_definition_version": "1.0",
                },
            },
        )
    run_id = app.extensions["run_queue"].create_run(
        {"name": "Modes", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    client = FakeJudgeClient()
    pipeline = WorkerPipeline(
        app.extensions["run_queue"],
        lambda case, profile: TargetOutcome(content="Candidate"),
        judge=JudgeCaller(
            app.extensions["run_queue"],
            SQLiteEndpointRepository(app.extensions["database_factory"]),
            client,  # type: ignore[arg-type]
            app.extensions["provider_call_executor"],
        ),
        heartbeat_interval=0.01,
    )
    while pipeline.run_once("snapshot-execution"):
        pass

    assert app.extensions["run_queue"].get_run(run_id)["status"] == "completed"
    assert len(client.prompts) == 3
    connection = app.extensions["database_factory"].connect()
    try:
        results = {
            row["case_id"]: json.loads(row["result_json"])
            for row in connection.execute(
                """SELECT c.case_id,r.result_json FROM run_results r
                JOIN run_case_snapshots c ON c.id=r.case_snapshot_id WHERE r.run_id=?""",
                (str(run_id),),
            )
        }
    finally:
        connection.close()
    assert Decimal(results["single_prompt"]["metadata"]["judge_cost"]) > 0
    assert len(results["single_prompt"]["metadata"]["provider_calls"]) == 1
    assert len(results["per_metric"]["metadata"]["provider_calls"]) == 2
    assert results["per_metric"]["metadata"]["mode"] == "per_metric"


def test_failed_per_metric_judge_persists_completed_call_evidence_without_score(
    tmp_path: Path,
) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["judge_profile_service"].create(
        {"name": "Judge model", "model_profile_id": str(model.id), "is_global_default": True}
    )
    definitions = app.extensions["judge_definition_service"]
    definition = definitions.create(
        {
            "name": "Audited failure",
            "mode": "per_metric",
            "threshold": "0.5",
            "metrics": [
                {"metric_id": "correctness", "weight": "1", "instructions": "Facts."},
                {"metric_id": "relevance", "weight": "1", "instructions": "Focus."},
            ],
        }
    )
    definitions.publish(definition.id)
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "partial-audit",
            "prompt": "Question",
            "evaluation_method": "llm_judge",
            "evaluator_config": {
                "judge_definition_id": definition.id,
                "judge_definition_version": "1.0",
            },
        },
    )
    run_id = app.extensions["run_queue"].create_run(
        {
            "name": "Audit failure",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )
    client = FailingLaterMetricJudgeClient()
    pipeline = WorkerPipeline(
        app.extensions["run_queue"],
        lambda case, profile: TargetOutcome(content="Candidate"),
        judge=JudgeCaller(
            app.extensions["run_queue"],
            SQLiteEndpointRepository(app.extensions["database_factory"]),
            client,  # type: ignore[arg-type]
            app.extensions["provider_call_executor"],
        ),
        heartbeat_interval=0.01,
    )
    while pipeline.run_once("partial-audit"):
        pass

    connection = app.extensions["database_factory"].connect()
    try:
        row = connection.execute(
            """SELECT r.status,r.score,r.result_json,r.judge_cost FROM run_results r
            JOIN run_case_snapshots c ON c.id=r.case_snapshot_id
            WHERE r.run_id=? AND c.case_id='partial-audit'""",
            (str(run_id),),
        ).fetchone()
    finally:
        connection.close()
    result = json.loads(row["result_json"])
    assert row["status"] == "error" and row["score"] is None
    assert Decimal(row["judge_cost"]) > 0
    assert result["incomplete"] is True
    assert len(result["provider_calls"]) == 3
    assert [call["provider_request_id"] for call in result["provider_calls"]] == [
        "fake-judge",
        "failed-parse-2",
        "failed-parse-3",
    ]
    assert "criteria" not in result and "candidate" not in result


def test_cancelled_per_metric_judge_stops_calls_and_persists_completed_call_evidence(
    tmp_path: Path,
) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["judge_profile_service"].create(
        {"name": "Judge model", "model_profile_id": str(model.id), "is_global_default": True}
    )
    definitions = app.extensions["judge_definition_service"]
    definition = definitions.create(
        {
            "name": "Cancelled audit",
            "mode": "per_metric",
            "threshold": "0.5",
            "metrics": [
                {"metric_id": "correctness", "weight": "1", "instructions": "Facts."},
                {"metric_id": "relevance", "weight": "1", "instructions": "Focus."},
            ],
        }
    )
    definitions.publish(definition.id)
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "cancelled-audit",
            "prompt": "Question",
            "evaluation_method": "llm_judge",
            "evaluator_config": {
                "judge_definition_id": definition.id,
                "judge_definition_version": "1.0",
            },
        },
    )
    run_id = app.extensions["run_queue"].create_run(
        {
            "name": "Cancel audit",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )
    cancelled = threading.Event()
    client = CancellingJudgeClient(cancelled)
    pipeline = WorkerPipeline(
        app.extensions["run_queue"],
        lambda case, profile: TargetOutcome(content="Candidate"),
        judge=JudgeCaller(
            app.extensions["run_queue"],
            SQLiteEndpointRepository(app.extensions["database_factory"]),
            client,  # type: ignore[arg-type]
            app.extensions["provider_call_executor"],
            cancelled,
        ),
        heartbeat_interval=0.01,
    )
    while pipeline.run_once("cancelled-audit", cancelled=cancelled):
        if cancelled.is_set():
            cancelled.clear()

    connection = app.extensions["database_factory"].connect()
    try:
        row = connection.execute(
            """SELECT r.status,r.score,r.result_json,r.judge_cost FROM run_results r
            JOIN run_case_snapshots c ON c.id=r.case_snapshot_id
            WHERE r.run_id=? AND c.case_id='cancelled-audit'""",
            (str(run_id),),
        ).fetchone()
    finally:
        connection.close()
    result = json.loads(row["result_json"])
    assert len(client.prompts) == 1
    assert row["status"] == "error" and row["score"] is None
    assert Decimal(row["judge_cost"]) > 0
    assert result["outcome_code"] == "judge_cancelled"
    assert result["incomplete"] is True
    assert len(result["provider_calls"]) == 1


def test_run_rejects_unknown_published_judge_version_atomically(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    app.extensions["judge_profile_service"].create(
        {"name": "Judge model", "model_profile_id": str(model.id), "is_global_default": True}
    )
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "missing-version",
            "prompt": "Question",
            "evaluation_method": "llm_judge",
            "evaluator_config": {
                "judge_definition_id": "missing",
                "judge_definition_version": "1.0",
            },
        },
    )
    queue = app.extensions["run_queue"]
    with pytest.raises(ValidationApiError, match="Published judge definition version not found"):
        queue.create_run(
            {
                "name": "Rejected",
                "golden_set_id": str(golden.id),
                "model_profile_ids": [str(model.id)],
            }
        )
    connection = app.extensions["database_factory"].connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        connection.close()


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
