from __future__ import annotations

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
        ]
    finally:
        connection.close()


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
