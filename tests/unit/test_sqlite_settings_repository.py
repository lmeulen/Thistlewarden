from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.database import SQLiteConnectionFactory
from evaluator.migrations import MigrationManager
from evaluator.repositories import SQLiteSettingsRepository
from evaluator.settings import SettingSource, SettingsResolver

pytestmark = pytest.mark.unit


def test_repository_maps_runtime_strings_and_resolver_ignores_bootstrap_records(
    tmp_path: Path,
) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "settings.db")
    MigrationManager(factory).migrate()
    repository = SQLiteSettingsRepository(factory)
    repository.set("run_retention_days", "7")
    repository.set("port", "5123")
    repository.set("database_path", "custom/evaluator.db")

    resolved = SettingsResolver(repository).resolve({})

    assert resolved.values.run_retention_days == 7
    assert resolved.get("run_retention_days").source is SettingSource.DATABASE
    assert resolved.values.port == 5000
    assert resolved.values.database_path == Path("data/evaluator.db")
    assert resolved.get("port").source is SettingSource.DEFAULT


def test_repository_set_update_delete_and_all(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "settings.db")
    MigrationManager(factory).migrate()
    repository = SQLiteSettingsRepository(factory)

    repository.set("port", "5001")
    repository.set("port", "5002")
    repository.set("run_retention_days", "7")

    assert repository.get("port") == "5002"
    assert repository.all() == {"port": "5002", "run_retention_days": "7"}
    repository.delete("port")
    assert repository.get("port") is None
