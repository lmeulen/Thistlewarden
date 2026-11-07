from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.database import SQLiteConnectionFactory
from evaluator.migrations import MigrationManager
from evaluator.repositories import SQLiteSettingsRepository
from evaluator.settings import SettingSource, SettingsResolver

pytestmark = pytest.mark.unit


def test_repository_maps_database_strings_to_domain_types(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "settings.db")
    MigrationManager(factory).migrate()
    repository = SQLiteSettingsRepository(factory)
    repository.set("port", "5123")
    repository.set("workers_enabled", "false")
    repository.set("database_path", "custom/evaluator.db")

    resolved = SettingsResolver(repository).resolve({})

    assert resolved.values.port == 5123
    assert resolved.values.workers_enabled is False
    assert resolved.values.database_path == Path("custom/evaluator.db")
    assert resolved.get("port").source is SettingSource.DATABASE


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
