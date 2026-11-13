"""Transactional, versioned SQLite schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.time import to_utc_iso, utc_now

MigrationAction = Callable[[sqlite3.Connection], None]
MigrationHook = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: MigrationAction


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE settings (
            name TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE settings ADD COLUMN updated_at TEXT")
    connection.execute("UPDATE settings SET updated_at = created_at WHERE updated_at IS NULL")


def _migration_3(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE secret_records (
            id TEXT PRIMARY KEY NOT NULL,
            kind TEXT NOT NULL CHECK(length(kind) > 0),
            ciphertext BLOB NOT NULL CHECK(length(ciphertext) > 16),
            nonce BLOB NOT NULL CHECK(length(nonce) = 12),
            key_version INTEGER NOT NULL CHECK(key_version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _migration_4(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE endpoints (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(name) > 0),
            completions_url TEXT NOT NULL,
            models_url TEXT,
            api_key_secret_id TEXT REFERENCES secret_records(id) ON DELETE RESTRICT,
            api_key_header TEXT NOT NULL,
            api_key_prefix TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds > 0),
            max_retries INTEGER NOT NULL CHECK(max_retries >= 0),
            requests_per_minute INTEGER CHECK(requests_per_minute > 0),
            tokens_per_minute INTEGER CHECK(tokens_per_minute > 0),
            max_concurrency INTEGER CHECK(max_concurrency > 0),
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            trusted_internal_http INTEGER NOT NULL CHECK(trusted_internal_http IN (0, 1)),
            security_warning_confirmed INTEGER NOT NULL CHECK(security_warning_confirmed IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE endpoint_headers (
            id TEXT PRIMARY KEY NOT NULL,
            endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
            name TEXT NOT NULL COLLATE NOCASE,
            value TEXT,
            secret_id TEXT REFERENCES secret_records(id) ON DELETE RESTRICT,
            UNIQUE(endpoint_id, name),
            CHECK((value IS NOT NULL AND secret_id IS NULL) OR
                  (value IS NULL AND secret_id IS NOT NULL))
        )
        """
    )


def _migration_5(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE model_profiles (
            id TEXT PRIMARY KEY NOT NULL,
            endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE RESTRICT,
            model_id TEXT NOT NULL CHECK(length(model_id) > 0),
            display_name TEXT NOT NULL COLLATE NOCASE CHECK(length(display_name) > 0),
            description TEXT,
            temperature TEXT,
            top_p TEXT,
            max_tokens INTEGER CHECK(max_tokens > 0),
            reasoning_field_path TEXT,
            reasoning_value TEXT,
            send_reasoning_off INTEGER NOT NULL CHECK(send_reasoning_off IN (0, 1)),
            custom_parameters_json TEXT NOT NULL,
            input_per_million TEXT NOT NULL,
            output_per_million TEXT NOT NULL,
            cached_input_per_million TEXT,
            reasoning_per_million TEXT,
            fixed_per_request TEXT,
            context_threshold INTEGER CHECK(context_threshold > 0),
            high_context_input_per_million TEXT,
            high_context_output_per_million TEXT,
            high_context_cached_input_per_million TEXT,
            high_context_reasoning_per_million TEXT,
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(endpoint_id, display_name)
        )
        """
    )
    connection.execute(
        """
        CREATE TRIGGER archive_models_on_endpoint_deactivation
        AFTER UPDATE OF active ON endpoints
        WHEN OLD.active = 1 AND NEW.active = 0
        BEGIN
            UPDATE model_profiles
            SET active = 0, updated_at = NEW.updated_at
            WHERE endpoint_id = NEW.id AND active = 1;
        END
        """
    )


def _migration_6(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE cost_calculations (
            id TEXT PRIMARY KEY NOT NULL,
            model_profile_id TEXT NOT NULL REFERENCES model_profiles(id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK(status IN ('calculated', 'unknown')),
            reason TEXT,
            usage_json TEXT NOT NULL,
            pricing_json TEXT NOT NULL,
            selected_tier TEXT CHECK(selected_tier IN ('standard', 'high_context')),
            regular_input_tokens INTEGER CHECK(regular_input_tokens >= 0),
            regular_output_tokens INTEGER CHECK(regular_output_tokens >= 0),
            input_cost TEXT,
            cached_input_cost TEXT,
            output_cost TEXT,
            reasoning_cost TEXT,
            fixed_cost TEXT NOT NULL,
            total_cost TEXT,
            chargeable INTEGER NOT NULL CHECK(chargeable IN (0, 1)),
            created_at TEXT NOT NULL,
            CHECK(
                (status = 'calculated' AND total_cost IS NOT NULL AND selected_tier IS NOT NULL)
                OR (status = 'unknown' AND total_cost IS NULL AND selected_tier IS NULL)
            )
        )
        """
    )


def _migration_7(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_sets (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(name) > 0),
            description TEXT,
            version TEXT NOT NULL CHECK(length(version) > 0),
            tags_json TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE golden_set_items (
            id TEXT PRIMARY KEY NOT NULL,
            golden_set_id TEXT NOT NULL REFERENCES golden_sets(id) ON DELETE CASCADE,
            case_id TEXT NOT NULL COLLATE NOCASE CHECK(length(case_id) > 0),
            prompt TEXT NOT NULL CHECK(length(prompt) > 0),
            system_prompt TEXT,
            expected_answer TEXT,
            evaluation_method TEXT NOT NULL CHECK(evaluation_method IN (
                'exact_match', 'contains', 'regex', 'numeric', 'json_schema', 'llm_judge'
            )),
            evaluator_config_json TEXT NOT NULL,
            category TEXT,
            tags_json TEXT NOT NULL,
            weight TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
            sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(golden_set_id, case_id),
            UNIQUE(golden_set_id, sort_order)
        )
        """
    )
    connection.execute(
        "CREATE INDEX idx_golden_set_items_filters "
        "ON golden_set_items(golden_set_id, enabled, category, sort_order)"
    )


def _migration_8(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE judge_profiles (
            id TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(name) > 0),
            model_profile_id TEXT NOT NULL REFERENCES model_profiles(id) ON DELETE RESTRICT,
            prompt_template TEXT NOT NULL,
            template_version TEXT NOT NULL,
            threshold TEXT NOT NULL,
            rubric_json TEXT NOT NULL,
            temperature TEXT NOT NULL,
            max_output_tokens INTEGER CHECK(max_output_tokens > 0),
            is_global_default INTEGER NOT NULL CHECK(is_global_default IN (0,1)),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE UNIQUE INDEX one_global_judge ON judge_profiles(is_global_default) "
        "WHERE is_global_default = 1"
    )
    connection.execute(
        "ALTER TABLE golden_sets ADD COLUMN judge_profile_id TEXT "
        "REFERENCES judge_profiles(id) ON DELETE RESTRICT"
    )


def _migration_9(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, golden_set_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending','running','completed','completed_with_errors','failed','cancelled'
            )),
            judge_profile_id TEXT, note TEXT, cancelled_at TEXT, created_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, recovery_warning INTEGER NOT NULL DEFAULT 0,
            judge_snapshot_json TEXT
        );
        CREATE TABLE run_case_snapshots (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            source_item_id TEXT NOT NULL, case_id TEXT NOT NULL, weight TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, UNIQUE(run_id, source_item_id)
        );
        CREATE TABLE run_model_snapshots (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            source_profile_id TEXT NOT NULL, endpoint_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            UNIQUE(run_id, source_profile_id)
        );
        CREATE TABLE run_jobs (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            case_snapshot_id TEXT NOT NULL REFERENCES run_case_snapshots(id),
            model_snapshot_id TEXT NOT NULL REFERENCES run_model_snapshots(id),
            status TEXT NOT NULL CHECK(status IN (
                'pending','running','completed','failed','cancelled'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0, lease_owner TEXT,
            lease_generation INTEGER NOT NULL DEFAULT 0, lease_expires_at TEXT,
            recovered_after_interruption INTEGER NOT NULL DEFAULT 0, error_code TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(run_id, case_snapshot_id, model_snapshot_id)
        );
        CREATE INDEX idx_run_jobs_claim ON run_jobs(status, lease_expires_at, created_at);
        CREATE TABLE run_attempts (
            id TEXT PRIMARY KEY NOT NULL,
            job_id TEXT NOT NULL REFERENCES run_jobs(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL, category TEXT, metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(job_id, attempt_number)
        );
        CREATE TABLE run_results (
            id TEXT PRIMARY KEY NOT NULL,
            job_id TEXT NOT NULL UNIQUE REFERENCES run_jobs(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            model_snapshot_id TEXT NOT NULL, case_snapshot_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('passed','failed','error')),
            score TEXT, weight TEXT NOT NULL, result_json TEXT NOT NULL,
            target_cost TEXT, judge_cost TEXT, target_latency_ms TEXT, ttft_ms TEXT, tps TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER,
            reasoning_tokens INTEGER, created_at TEXT NOT NULL
        );
        """
    )


def _migration_10(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE golden_sets ADD COLUMN quality_gates_json TEXT")
    connection.execute("ALTER TABLE runs ADD COLUMN quality_gates_snapshot_json TEXT")
    connection.execute(
        "ALTER TABLE runs ADD COLUMN gate_status TEXT NOT NULL DEFAULT 'not_evaluated'"
    )
    connection.execute(
        """CREATE TABLE quality_gate_results (
            id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            model_snapshot_id TEXT,
            gate TEXT NOT NULL,
            measured_value TEXT,
            threshold_value TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('passed','failed','indeterminate')),
            created_at TEXT NOT NULL,
            UNIQUE(run_id, model_snapshot_id, gate)
        )"""
    )


def _migration_11(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX idx_run_results_filter ON run_results(run_id,status,model_snapshot_id)"
    )
    connection.execute("CREATE INDEX idx_run_results_case ON run_results(run_id,case_snapshot_id)")
    connection.execute("CREATE INDEX idx_run_models_run ON run_model_snapshots(run_id,id)")
    connection.execute("CREATE INDEX idx_run_cases_run ON run_case_snapshots(run_id,case_id)")


def _migration_12(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE model_profiles ADD COLUMN max_tokens_field_path "
        "TEXT NOT NULL DEFAULT 'max_tokens'"
    )


def _migration_13(connection: sqlite3.Connection) -> None:
    """Remove legacy free-form LLM-judge cases instead of guessing preset semantics."""

    legacy_run_ids = connection.execute(
        """SELECT DISTINCT r.id
        FROM runs r
        JOIN run_case_snapshots s ON s.run_id=r.id
        WHERE r.status IN ('pending','running')
          AND json_extract(s.snapshot_json,'$.evaluation_method')='llm_judge'"""
    ).fetchall()
    run_ids = [row["id"] for row in legacy_run_ids]
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        connection.execute(
            f"UPDATE run_jobs SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL "
            f"WHERE run_id IN ({placeholders}) AND status IN ('pending','running')",
            run_ids,
        )
        connection.execute(
            f"UPDATE runs SET status='cancelled',cancelled_at=COALESCE(cancelled_at,created_at) "
            f"WHERE id IN ({placeholders})",
            run_ids,
        )
    connection.execute("DELETE FROM golden_set_items WHERE evaluation_method='llm_judge'")


def _migration_14(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE judge_profiles ADD COLUMN temperature_enabled INTEGER NOT NULL DEFAULT 1 "
        "CHECK(temperature_enabled IN (0,1))"
    )
    connection.execute("ALTER TABLE judge_profiles ADD COLUMN reasoning_value TEXT")
    connection.execute(
        """UPDATE runs
        SET judge_snapshot_json=json_set(
            judge_snapshot_json,'$.temperature_enabled',1,'$.reasoning_value',NULL
        )
        WHERE status IN ('pending','running') AND judge_snapshot_json IS NOT NULL"""
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "create_settings", _migration_1),
    Migration(2, "add_settings_updated_at", _migration_2),
    Migration(3, "create_secret_records", _migration_3),
    Migration(4, "create_endpoints", _migration_4),
    Migration(5, "create_model_profiles", _migration_5),
    Migration(6, "create_cost_calculations", _migration_6),
    Migration(7, "create_golden_sets", _migration_7),
    Migration(8, "create_judge_profiles", _migration_8),
    Migration(9, "create_runs_and_queue", _migration_9),
    Migration(10, "create_quality_gates", _migration_10),
    Migration(11, "add_run_query_indexes", _migration_11),
    Migration(12, "add_model_parameter_bindings", _migration_12),
    Migration(13, "drop_legacy_llm_judge_test_cases", _migration_13),
    Migration(14, "add_judge_inference_overrides", _migration_14),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


class MigrationError(RuntimeError):
    """Raised when schema migration or restoration cannot complete safely."""


class SQLiteBackupService:
    """Create and restore consistent SQLite backups using SQLite's backup API."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Backup already exists: {destination}")
        source = self._factory.connect(create_parent=False)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise MigrationError("Backup failed integrity_check")
        except BaseException:
            target.close()
            source.close()
            destination.unlink(missing_ok=True)
            raise
        else:
            target.close()
            source.close()
        return destination

    @staticmethod
    def restore(source: Path, destination: Path) -> Path:
        if not source.is_file():
            raise FileNotFoundError(f"Backup does not exist: {source}")
        if destination.exists():
            raise FileExistsError(f"Restore destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(target_connection)
            result = target_connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise MigrationError("Restored database failed integrity_check")
        except BaseException:
            target_connection.close()
            source_connection.close()
            destination.unlink(missing_ok=True)
            raise
        else:
            target_connection.close()
            source_connection.close()
        return destination


_MIGRATION_LOCK = threading.Lock()


class MigrationManager:
    """Serialize and apply pending migrations in one exclusive transaction."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        migrations: Sequence[Migration] = MIGRATIONS,
        backup_directory: Path | None = None,
        before_apply: MigrationHook | None = None,
    ) -> None:
        self._factory = factory
        self._migrations = tuple(migrations)
        self._backup_directory = backup_directory or factory.database_path.parent / "backups"
        self._before_apply = before_apply
        versions = [migration.version for migration in self._migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("migration versions must be consecutive and start at 1")

    @staticmethod
    def _ensure_history_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def current_version(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if row is None:
            return 0
        version_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(version_row[0]) if version_row is not None else 0

    def migrate(self) -> int:
        # MVP supports one process; serialize concurrent startup threads because
        # sqlite3.executescript() used by legacy migration 9 commits implicitly.
        with _MIGRATION_LOCK:
            return self._migrate_locked()

    def _migrate_locked(self) -> int:
        database_existed = self._factory.database_path.exists()
        connection = self._factory.connect()
        try:
            original_version = self.current_version(connection)
            pending = [m for m in self._migrations if m.version > original_version]
            if not pending:
                return original_version

            # Existing non-baseline schemas get a unique online backup before upgrade.
            if database_existed and original_version > 0:
                backup_name = (
                    f"{self._factory.database_path.stem}-v{original_version}-"
                    f"{utc_now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex}.db"
                )
                SQLiteBackupService(self._factory).backup(self._backup_directory / backup_name)

            with transaction(connection, mode="EXCLUSIVE"):
                self._ensure_history_table(connection)
                # Re-read under lock: another startup may have completed while this one waited.
                locked_version = self.current_version(connection)
                for migration in self._migrations:
                    if migration.version <= locked_version:
                        continue
                    if self._before_apply is not None:
                        self._before_apply(migration.version)
                    migration.apply(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.name, to_utc_iso(utc_now())),
                    )
            return self.current_version(connection)
        finally:
            connection.close()
