from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evaluator.database import SQLiteConnectionFactory
from evaluator.migrations import MigrationManager
from evaluator.run_comparisons import RunComparisonService

pytestmark = pytest.mark.unit


def _service(tmp_path: Path) -> tuple[RunComparisonService, SQLiteConnectionFactory, UUID, UUID]:
    factory = SQLiteConnectionFactory(tmp_path / "comparison.db")
    MigrationManager(factory).migrate()
    baseline, candidate = uuid4(), uuid4()
    connection = factory.connect()
    try:
        for run_id, name in ((baseline, "Baseline"), (candidate, "Candidate")):
            connection.execute(
                """INSERT INTO runs(id,name,golden_set_id,status,created_at,completed_at,
                recovery_warning,judge_snapshot_json,gate_status)
                VALUES (?,?,?,'completed','2026-01-01T00:00:00Z','2026-01-01T01:00:00Z',0,?,
                'not_evaluated')""",
                (str(run_id), name, "set", json.dumps({"preset_id": "p", "version": "1"})),
            )
        connection.commit()
    finally:
        connection.close()
    return RunComparisonService(factory), factory, candidate, baseline


def _insert(
    factory: SQLiteConnectionFactory,
    run_id: UUID,
    *,
    source_case: str,
    source_model: str = "model",
    status: str = "passed",
    score: str | None = "1",
    prompt: str = "prompt",
    user_prompts: list[str] | None = None,
    model_id: str = "m",
    metric: str | None = "10",
) -> UUID:
    case_snapshot, model_snapshot, job, result = uuid4(), uuid4(), uuid4(), uuid4()
    if source_model == "model":
        source_model = f"model-{source_case}"
    case = {
        "case_id": source_case,
        "evaluation_method": "exact_match",
        "evaluator_config_json": '{"expected_value":"a"}',
        "weight": "1",
        **(
            {"snapshot_schema_version": "2.0", "user_prompts": user_prompts}
            if user_prompts is not None
            else {"prompt": prompt}
        ),
    }
    model = {
        "model_id": model_id,
        "display_name": "Model",
        "temperature": "0",
        "input_per_million": "1",
    }
    connection = factory.connect()
    try:
        connection.execute(
            "INSERT INTO run_case_snapshots VALUES (?,?,?,?,?,?)",
            (str(case_snapshot), str(run_id), source_case, source_case, "1", json.dumps(case)),
        )
        connection.execute(
            "INSERT INTO run_model_snapshots VALUES (?,?,?,?,?)",
            (str(model_snapshot), str(run_id), source_model, "endpoint", json.dumps(model)),
        )
        connection.execute(
            """INSERT INTO run_jobs(id,run_id,case_snapshot_id,model_snapshot_id,status,
            created_at,updated_at) VALUES (?,?,?,?,?,'now','now')""",
            (
                str(job),
                str(run_id),
                str(case_snapshot),
                str(model_snapshot),
                "failed" if status == "error" else "completed",
            ),
        )
        connection.execute(
            """INSERT INTO run_results(id,job_id,run_id,model_snapshot_id,case_snapshot_id,
            status,score,weight,result_json,target_cost,judge_cost,target_latency_ms,ttft_ms,tps,
            input_tokens,output_tokens,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(result),
                str(job),
                str(run_id),
                str(model_snapshot),
                str(case_snapshot),
                status,
                score,
                "1",
                "{}",
                metric,
                None,
                metric,
                metric,
                metric,
                int(metric) if metric else None,
                int(metric) if metric else None,
                "now",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return result


def test_transitions_deltas_errors_changes_and_missing_records(tmp_path: Path) -> None:
    service, factory, candidate, baseline = _service(tmp_path)
    _insert(factory, baseline, source_case="regression", status="passed", score="1", metric="10")
    _insert(factory, candidate, source_case="regression", status="failed", score="0", metric="12")
    _insert(factory, baseline, source_case="improvement", status="failed", score="0")
    _insert(factory, candidate, source_case="improvement", status="passed", score="1")
    _insert(factory, baseline, source_case="error", status="error", score=None)
    _insert(factory, candidate, source_case="error", status="passed")
    _insert(factory, baseline, source_case="changed", prompt="old")
    _insert(factory, candidate, source_case="changed", prompt="new")
    _insert(factory, baseline, source_case="removed")
    _insert(factory, candidate, source_case="added")
    _insert(factory, baseline, source_case="missing-metric", metric=None)
    _insert(factory, candidate, source_case="missing-metric", metric="1")

    report = service.compare(candidate, baseline)
    assert report["compatibility"] == "partially_compatible"
    assert report["summary"] == {
        "total_records": 7,
        "comparable": 3,
        "regressions": 1,
        "improvements": 1,
        "unchanged": 1,
        "incomparable": 4,
        "added": 1,
        "removed": 1,
        "changed": 1,
    }
    rows = {row["case_id"]: row for row in report["records"]}
    assert rows["regression"]["metric_deltas"]["score"] == "-1"
    assert rows["regression"]["metric_deltas"]["target_latency_ms"] == "2"
    assert rows["error"]["change_kind"] == "result_error"
    assert rows["changed"]["change_kind"] == "changed"
    assert rows["missing-metric"]["metric_deltas"]["target_cost"] is None
    assert report["aggregate_deltas"]["error_rate"] == "-0.25"


def test_legacy_and_versioned_single_turn_snapshots_compare_semantically(tmp_path: Path) -> None:
    service, factory, candidate, baseline = _service(tmp_path)
    _insert(factory, baseline, source_case="same", prompt="prompt")
    _insert(factory, candidate, source_case="same", user_prompts=["prompt"])
    _insert(factory, baseline, source_case="changed", prompt="prompt")
    _insert(factory, candidate, source_case="changed", user_prompts=["prompt", "follow-up"])

    rows = {row["case_id"]: row for row in service.compare(candidate, baseline)["records"]}

    assert rows["same"]["change_kind"] == "none"
    assert rows["changed"]["change_kind"] == "changed"


def test_missing_result_is_visible_and_incomparable(tmp_path: Path) -> None:
    service, factory, candidate, baseline = _service(tmp_path)
    _insert(factory, baseline, source_case="missing", status="passed")
    _insert(factory, candidate, source_case="missing", status="passed")
    connection = factory.connect()
    try:
        connection.execute("DELETE FROM run_results WHERE run_id=?", (str(candidate),))
        connection.commit()
    finally:
        connection.close()
    report = service.compare(candidate, baseline)
    assert report["summary"]["incomparable"] == 1
    assert report["records"][0]["change_kind"] == "missing_result"
    assert "missing result" in report["warnings"][0]


def test_filter_candidates_and_deterministic_export(tmp_path: Path) -> None:
    service, factory, candidate, baseline = _service(tmp_path)
    baseline_result = _insert(factory, baseline, source_case="case", status="passed")
    candidate_result = _insert(factory, candidate, source_case="case", status="failed")
    assert service.candidates(candidate)[0]["compatibility"] == "compatible"
    filtered = service.compare(candidate, baseline, filter_="regression")
    assert len(filtered["records"]) == 1
    assert filtered["records"][0]["baseline_result_id"] == str(baseline_result)
    assert filtered["records"][0]["candidate_result_id"] == str(candidate_result)
    first = service.export_json(candidate, baseline)
    assert first == service.export_json(candidate, baseline)
    assert json.loads(first)["methodology"]["errors"].startswith("Errors are incomparable")
