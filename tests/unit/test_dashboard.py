from __future__ import annotations

from pathlib import Path

from evaluator.dashboard import DashboardService, DashboardStatistics
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
    )
