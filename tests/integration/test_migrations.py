from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from evaluator.database import SQLiteConnectionFactory
from evaluator.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    MigrationManager,
    SQLiteBackupService,
)
from evaluator.repositories import SQLiteSettingsRepository

pytestmark = pytest.mark.integration


def _version(factory: SQLiteConnectionFactory) -> int:
    connection = factory.connect()
    try:
        return MigrationManager.current_version(connection)
    finally:
        connection.close()


def _create_version_one_fixture(path: Path) -> None:
    factory = SQLiteConnectionFactory(path)
    MigrationManager(factory, migrations=MIGRATIONS[:1]).migrate()
    connection = factory.connect()
    try:
        connection.execute(
            "INSERT INTO settings(name, value, created_at) VALUES (?, ?, ?)",
            ("port", "5111", "2026-01-01T00:00:00Z"),
        )
    finally:
        connection.close()


def test_empty_database_migrates_to_latest_and_is_idempotent(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "app.db")
    manager = MigrationManager(factory)

    assert manager.migrate() == LATEST_SCHEMA_VERSION
    assert manager.migrate() == LATEST_SCHEMA_VERSION
    assert _version(factory) == LATEST_SCHEMA_VERSION

    connection = factory.connect()
    try:
        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["name"]) for row in rows] == [
            (1, "create_settings"),
            (2, "add_settings_updated_at"),
            (3, "create_secret_records"),
            (4, "create_endpoints"),
            (5, "create_model_profiles"),
            (6, "create_cost_calculations"),
            (7, "create_golden_sets"),
            (8, "create_judge_profiles"),
            (9, "create_runs_and_queue"),
            (10, "create_quality_gates"),
            (11, "add_run_query_indexes"),
            (12, "add_model_parameter_bindings"),
            (13, "drop_legacy_llm_judge_test_cases"),
            (14, "add_judge_inference_overrides"),
            (15, "add_versioned_judge_implementations"),
        ]
    finally:
        connection.close()


def test_migration_14_preserves_legacy_temperature_and_active_snapshots(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "app.db")
    MigrationManager(factory, migrations=MIGRATIONS[:13]).migrate()
    connection = factory.connect()
    try:
        connection.execute(
            "INSERT INTO runs(id,name,golden_set_id,status,created_at,recovery_warning,"
            "judge_snapshot_json) VALUES (?,?,?,?,?,?,?)",
            ("r", "Run", "s", "running", "2026-01-01T00:00:00Z", 0, '{"temperature":"0.3"}'),
        )
        connection.commit()
    finally:
        connection.close()

    assert MigrationManager(factory, migrations=MIGRATIONS[:14]).migrate() == 14
    connection = factory.connect()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(judge_profiles)")}
        snapshot = json.loads(
            connection.execute("SELECT judge_snapshot_json FROM runs WHERE id='r'").fetchone()[0]
        )
    finally:
        connection.close()
    assert {"temperature_enabled", "reasoning_value"} <= columns
    assert snapshot["temperature_enabled"] == 1
    assert snapshot["reasoning_value"] is None


def test_migration_15_versions_profiles_and_historical_snapshots(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "app.db")
    MigrationManager(factory, migrations=MIGRATIONS[:14]).migrate()
    connection = factory.connect()
    try:
        connection.execute(
            "INSERT INTO runs(id,name,golden_set_id,status,created_at,recovery_warning,"
            "judge_snapshot_json) VALUES (?,?,?,?,?,?,?)",
            ("r", "Run", "s", "completed", "2026-01-01T00:00:00Z", 0, '{"name":"J"}'),
        )
        connection.commit()
    finally:
        connection.close()

    assert MigrationManager(factory).migrate() == 15
    connection = factory.connect()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(judge_profiles)")}
        snapshot = json.loads(
            connection.execute("SELECT judge_snapshot_json FROM runs WHERE id='r'").fetchone()[0]
        )
    finally:
        connection.close()
    assert {
        "implementation_id",
        "implementation_version",
        "implementation_config_json",
    } <= columns
    assert snapshot["implementation_id"] == "openai-compatible"
    assert snapshot["implementation_version"] == "1.0"
    assert snapshot["implementation_config"] == {}


def test_version_one_to_two_preserves_existing_data(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    _create_version_one_fixture(database)
    factory = SQLiteConnectionFactory(database)

    assert MigrationManager(factory).migrate() == LATEST_SCHEMA_VERSION

    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT name, value, created_at, updated_at FROM settings WHERE name = 'port'"
        ).fetchone()
        assert tuple(row) == ("port", "5111", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    finally:
        connection.close()
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_version_three_to_four_preserves_existing_secret_records(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    factory = SQLiteConnectionFactory(database)
    MigrationManager(factory, migrations=MIGRATIONS[:3]).migrate()
    connection = factory.connect()
    try:
        connection.execute(
            """
            INSERT INTO secret_records(
                id, kind, ciphertext, nonce, key_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "00000000-0000-0000-0000-000000000001",
                "fixture",
                b"ciphertext-longer-than-tag",
                b"123456789012",
                1,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
    finally:
        connection.close()

    assert MigrationManager(factory).migrate() == LATEST_SCHEMA_VERSION
    connection = factory.connect()
    try:
        assert connection.execute("SELECT kind FROM secret_records").fetchone()[0] == "fixture"
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'table' AND name IN ('endpoints', 'endpoint_headers')
                """
            ).fetchone()[0]
            == 2
        )
    finally:
        connection.close()


def test_version_four_to_five_preserves_endpoint_and_adds_profile_constraints(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.db"
    factory = SQLiteConnectionFactory(database)
    MigrationManager(factory, migrations=MIGRATIONS[:4]).migrate()
    connection = factory.connect()
    try:
        connection.execute(
            """
            INSERT INTO endpoints(
                id, name, completions_url, api_key_header, api_key_prefix,
                timeout_seconds, max_retries, active, trusted_internal_http,
                security_warning_confirmed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "00000000-0000-0000-0000-000000000010",
                "Existing endpoint",
                "https://example.com/v1",
                "Authorization",
                "",
                60,
                2,
                1,
                0,
                0,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
    finally:
        connection.close()

    assert MigrationManager(factory).migrate() == LATEST_SCHEMA_VERSION
    connection = factory.connect()
    try:
        assert connection.execute("SELECT name FROM endpoints").fetchone()[0] == "Existing endpoint"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='model_profiles'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_version_five_to_six_preserves_model_profiles_and_adds_cost_table(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.db"
    factory = SQLiteConnectionFactory(database)
    MigrationManager(factory, migrations=MIGRATIONS[:5]).migrate()
    connection = factory.connect()
    try:
        endpoint_id = "00000000-0000-0000-0000-000000000020"
        connection.execute(
            """INSERT INTO endpoints(
                id,name,completions_url,api_key_header,api_key_prefix,timeout_seconds,
                max_retries,active,trusted_internal_http,security_warning_confirmed,
                created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                endpoint_id,
                "Provider",
                "https://example.com",
                "Authorization",
                "",
                60,
                2,
                1,
                0,
                0,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.execute(
            """INSERT INTO model_profiles VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "00000000-0000-0000-0000-000000000021",
                endpoint_id,
                "model",
                "Profile",
                None,
                "0",
                None,
                None,
                None,
                None,
                0,
                "{}",
                "1.00000000",
                "2.00000000",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                1,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
    finally:
        connection.close()

    assert MigrationManager(factory).migrate() == LATEST_SCHEMA_VERSION
    connection = factory.connect()
    try:
        assert (
            connection.execute("SELECT display_name FROM model_profiles").fetchone()[0] == "Profile"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='cost_calculations'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_migration_13_removes_legacy_llm_judge_cases_and_cancels_active_runs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.db"
    factory = SQLiteConnectionFactory(database)
    MigrationManager(factory, migrations=MIGRATIONS[:12]).migrate()
    connection = factory.connect()
    try:
        timestamp = "2026-01-01T00:00:00Z"
        set_id = "40000000-0000-4000-8000-000000000001"
        item_id = "50000000-0000-4000-8000-000000000001"
        run_id = "60000000-0000-4000-8000-000000000001"
        snapshot_id = "70000000-0000-4000-8000-000000000001"
        connection.execute(
            """INSERT INTO golden_sets(
                id,name,description,version,tags_json,active,created_at,updated_at,judge_profile_id
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (set_id, "Legacy", None, "1.0", "[]", 1, timestamp, timestamp, None),
        )
        connection.execute(
            "INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                set_id,
                "legacy-judge",
                "Prompt",
                None,
                "Legacy reference",
                "llm_judge",
                '{"rubric":[{"id":"custom","description":"Custom"}],"threshold":"0.5"}',
                None,
                "[]",
                "1",
                1,
                0,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO runs(
                id,name,golden_set_id,status,created_at,started_at,recovery_warning
            ) VALUES (?,?,?,?,?,?,?)""",
            (run_id, "Legacy run", set_id, "running", timestamp, timestamp, 0),
        )
        connection.execute(
            "INSERT INTO run_case_snapshots VALUES (?,?,?,?,?,?)",
            (
                snapshot_id,
                run_id,
                item_id,
                "legacy-judge",
                "1",
                '{"evaluation_method":"llm_judge","evaluator_config_json":"{}"}',
            ),
        )
    finally:
        connection.close()

    assert MigrationManager(factory).migrate() == LATEST_SCHEMA_VERSION
    connection = factory.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM golden_set_items").fetchone()[0] == 0
        run = connection.execute(
            "SELECT status,cancelled_at FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        assert tuple(run) == ("cancelled", timestamp)
        assert connection.execute("SELECT COUNT(*) FROM run_case_snapshots").fetchone()[0] == 1
    finally:
        connection.close()


def test_failure_mid_migration_leaves_version_one_intact(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    _create_version_one_fixture(database)
    factory = SQLiteConnectionFactory(database)

    def fail_before_version_two(version: int) -> None:
        if version == 2:
            raise RuntimeError("injected migration failure")

    with pytest.raises(RuntimeError, match="injected"):
        MigrationManager(
            factory, migrations=MIGRATIONS[:2], before_apply=fail_before_version_two
        ).migrate()

    assert _version(factory) == 1
    connection = factory.connect()
    try:
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(settings)")]
        assert "updated_at" not in columns
        assert (
            connection.execute("SELECT value FROM settings WHERE name = 'port'").fetchone()[0]
            == "5111"
        )
    finally:
        connection.close()


def test_concurrent_migrations_are_serialized(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    factory = SQLiteConnectionFactory(database, busy_timeout_ms=10_000)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    versions: list[int] = []

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            versions.append(MigrationManager(factory).migrate())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(versions) == [LATEST_SCHEMA_VERSION, LATEST_SCHEMA_VERSION]
    connection = factory.connect()
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            == LATEST_SCHEMA_VERSION
        )
    finally:
        connection.close()


def test_backup_restores_to_new_database_and_can_migrate(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    _create_version_one_fixture(database)
    factory = SQLiteConnectionFactory(database)
    backup = tmp_path / "manual-backup.db"
    restored = tmp_path / "restored.db"

    SQLiteBackupService(factory).backup(backup)
    SQLiteBackupService.restore(backup, restored)
    restored_factory = SQLiteConnectionFactory(restored)

    assert MigrationManager(restored_factory).migrate() == LATEST_SCHEMA_VERSION
    assert SQLiteSettingsRepository(restored_factory).get("port") == "5111"


def test_two_repositories_share_persisted_settings(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "app.db")
    MigrationManager(factory).migrate()
    first = SQLiteSettingsRepository(factory)
    second = SQLiteSettingsRepository(factory)

    first.set("run_retention_days", "14")

    assert second.get("run_retention_days") == "14"


def test_foreign_keys_apply_on_every_factory_connection(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "app.db")
    MigrationManager(factory).migrate()
    connection = factory.connect()
    try:
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child(parent_id) VALUES (1)")
    finally:
        connection.close()
