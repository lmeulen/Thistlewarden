from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from evaluator.dashboard import DashboardService, DashboardStatistics, LatestRunStatistics
from evaluator.database import SQLiteConnectionFactory
from evaluator.migrations import MigrationManager


def _factory(tmp_path: Path) -> SQLiteConnectionFactory:
    factory = SQLiteConnectionFactory(tmp_path / "dashboard.db")
    MigrationManager(factory).migrate()
    return factory


def _populate(factory: SQLiteConnectionFactory) -> None:
    connection = factory.connect()
    try:
        endpoint_values = (
            "https://example.test/v1/chat/completions",
            "Authorization",
            "Bearer ",
            60,
            2,
            0,
            0,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        )
        connection.executemany(
            """INSERT INTO endpoints(
                id, name, completions_url, api_key_header, api_key_prefix,
                timeout_seconds, max_retries, active, trusted_internal_http,
                security_warning_confirmed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("endpoint-active", "Active", *endpoint_values[:5], 1, *endpoint_values[5:]),
                ("endpoint-archived", "Archived", *endpoint_values[:5], 0, *endpoint_values[5:]),
            ],
        )
        connection.executemany(
            """INSERT INTO model_profiles(
                id, endpoint_id, model_id, display_name, send_reasoning_off,
                custom_parameters_json, input_per_million, output_per_million,
                fixed_per_request, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, '{}', '0', '0', '0', ?, ?, ?)""",
            [
                (
                    "model-active",
                    "endpoint-active",
                    "a",
                    "Active",
                    1,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
                (
                    "model-disabled",
                    "endpoint-active",
                    "b",
                    "Disabled",
                    0,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
                (
                    "model-archived-parent",
                    "endpoint-archived",
                    "c",
                    "Archived parent",
                    1,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
            ],
        )
        connection.execute(
            "UPDATE model_profiles SET input_per_million='1.5' WHERE id='model-active'"
        )
        connection.execute(
            """INSERT INTO judge_profiles(
                id, name, model_profile_id, prompt_template, template_version, threshold,
                rubric_json, temperature, max_output_tokens, is_global_default, active,
                created_at, updated_at
            ) VALUES ('judge-active', 'Judge', 'model-active', 'Prompt', '1', '0.5',
                '{}', '0', 100, 1, 1, ?, ?)""",
            endpoint_values[-2:],
        )
        connection.executemany(
            """INSERT INTO golden_sets(
                id, name, version, tags_json, active, created_at, updated_at
            ) VALUES (?, ?, '1.0', '[]', ?, ?, ?)""",
            [
                ("set-active", "Active", 1, endpoint_values[-2], endpoint_values[-1]),
                ("set-archived", "Archived", 0, endpoint_values[-2], endpoint_values[-1]),
            ],
        )
        connection.executemany(
            """INSERT INTO golden_set_items(
                id, golden_set_id, case_id, prompt, evaluation_method,
                evaluator_config_json, tags_json, weight, enabled, sort_order,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'Prompt', 'exact_match', '{}', '[]', '1', ?, ?, ?, ?)""",
            [
                (
                    "case-enabled",
                    "set-active",
                    "enabled",
                    1,
                    0,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
                (
                    "case-disabled",
                    "set-active",
                    "disabled",
                    0,
                    1,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
                (
                    "case-archived-parent",
                    "set-archived",
                    "archived",
                    1,
                    0,
                    endpoint_values[-2],
                    endpoint_values[-1],
                ),
            ],
        )
        connection.execute(
            "UPDATE golden_set_items SET evaluation_method='llm_judge' WHERE id='case-disabled'"
        )
        connection.executemany(
            "INSERT INTO runs(id, name, golden_set_id, status, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                (f"run-{status}", status, "set-active", status, endpoint_values[-2])
                for status in (
                    "pending",
                    "running",
                    "completed",
                    "completed_with_errors",
                    "failed",
                    "cancelled",
                )
            ],
        )
    finally:
        connection.close()


def test_empty_database_returns_zero_statistics(tmp_path: Path) -> None:
    assert DashboardService(_factory(tmp_path)).statistics() == DashboardStatistics()


def test_statistics_apply_status_and_active_parent_definitions(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _populate(factory)

    assert DashboardService(factory).statistics() == DashboardStatistics(
        total_runs=6,
        active_runs=2,
        finished_runs=4,
        active_model_profiles=1,
        active_endpoints=1,
        total_model_profiles=3,
        enabled_cases=1,
        active_golden_sets=1,
        total_cases=3,
        priced_model_profiles=1,
        active_judge_profiles=1,
        total_judge_profiles=1,
        llm_judge_cases=1,
        latest_run=LatestRunStatistics(
            id="run-completed_with_errors",
            name="completed_with_errors",
            status="completed_with_errors",
            gate_status="not_evaluated",
            completed_at=None,
            weighted_score=None,
            pass_rate=None,
            error_rate=None,
            known_total_cost=None,
            known_cost_count=0,
            total_results=0,
        ),
    )


def test_latest_completed_run_metrics_are_exact(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    connection = factory.connect()
    try:
        connection.executemany(
            """INSERT INTO runs(
                id, name, golden_set_id, status, gate_status, created_at, completed_at
            ) VALUES (?, ?, 'set', ?, ?, ?, ?)""",
            [
                (
                    "run-older",
                    "Older",
                    "completed",
                    "passed",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T01:00:00Z",
                ),
                (
                    "run-latest",
                    "Latest",
                    "completed_with_errors",
                    "failed",
                    "2026-01-02T00:00:00Z",
                    "2026-01-02T01:00:00Z",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO run_model_snapshots VALUES (?, ?, ?, 'endpoint', '{}')",
            [
                ("model", "run-latest", "source-model"),
                ("model-older", "run-older", "source-model"),
            ],
        )
        connection.execute(
            """INSERT INTO run_case_snapshots
            VALUES ('case-older', 'run-older', 'source-older', 'older', '1', '{}')"""
        )
        connection.execute(
            """INSERT INTO run_jobs(
                id, run_id, case_snapshot_id, model_snapshot_id, status,
                attempt_count, lease_generation, created_at, updated_at
            ) VALUES ('job-older', 'run-older', 'case-older', 'model-older', 'completed',
                1, 0, '2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO run_results(
                id, job_id, run_id, model_snapshot_id, case_snapshot_id, status,
                score, weight, result_json, target_cost, created_at
            ) VALUES ('result-older', 'job-older', 'run-older', 'model-older', 'case-older',
                'passed', '1', '1', '{}', '0.01', '2026-01-01T01:00:00Z')"""
        )
        connection.executemany(
            "INSERT INTO run_case_snapshots VALUES (?, 'run-latest', ?, ?, '1', '{}')",
            [
                ("case-pass", "source-pass", "pass"),
                ("case-fail", "source-fail", "fail"),
                ("case-error", "source-error", "error"),
            ],
        )
        connection.executemany(
            """INSERT INTO run_jobs(
                id, run_id, case_snapshot_id, model_snapshot_id, status,
                attempt_count, lease_generation, created_at, updated_at
            ) VALUES (?, 'run-latest', ?, 'model', ?, 1, 0, ?, ?)""",
            [
                (
                    "job-pass",
                    "case-pass",
                    "completed",
                    "2026-01-02T00:00:00Z",
                    "2026-01-02T01:00:00Z",
                ),
                (
                    "job-fail",
                    "case-fail",
                    "completed",
                    "2026-01-02T00:00:00Z",
                    "2026-01-02T01:00:00Z",
                ),
                (
                    "job-error",
                    "case-error",
                    "failed",
                    "2026-01-02T00:00:00Z",
                    "2026-01-02T01:00:00Z",
                ),
            ],
        )
        connection.executemany(
            """INSERT INTO run_results(
                id, job_id, run_id, model_snapshot_id, case_snapshot_id, status,
                score, weight, result_json, target_cost, created_at
            ) VALUES (?, ?, 'run-latest', 'model', ?, ?, ?, ?, '{}', ?, ?)""",
            [
                (
                    "result-pass",
                    "job-pass",
                    "case-pass",
                    "passed",
                    "0.8",
                    "2",
                    "0.01",
                    "2026-01-02T01:00:00Z",
                ),
                (
                    "result-fail",
                    "job-fail",
                    "case-fail",
                    "failed",
                    "0.2",
                    "1",
                    "0.02",
                    "2026-01-02T01:00:00Z",
                ),
                (
                    "result-error",
                    "job-error",
                    "case-error",
                    "error",
                    None,
                    "9",
                    None,
                    "2026-01-02T01:00:00Z",
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    statistics = DashboardService(factory).statistics()
    latest = statistics.latest_run

    assert statistics.overall_pass_rate == Decimal(2) / Decimal(3)
    assert statistics.average_weighted_score == Decimal("0.8")
    assert latest is not None
    assert latest.id == "run-latest"
    assert latest.weighted_score == Decimal("0.6")
    assert latest.pass_rate == Decimal("0.5")
    assert latest.error_rate == Decimal(1) / Decimal(3)
    assert latest.known_total_cost == Decimal("0.03")
    assert latest.known_cost_count == 2
    assert latest.total_results == 3
