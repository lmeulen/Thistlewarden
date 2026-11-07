from __future__ import annotations

from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.config import ConfigurationError
from evaluator.settings import InMemorySettingsRepository, SettingSource

pytestmark = pytest.mark.integration


def test_app_starts_with_minimal_environment() -> None:
    app = create_app(env_file=None, settings_environment={})

    resolved = app.extensions["settings"]
    assert resolved.values.port == 5000
    assert resolved.get("port").source is SettingSource.DEFAULT


def test_app_refuses_invalid_concurrency_combination() -> None:
    with pytest.raises(ConfigurationError, match="default_run_concurrency"):
        create_app(
            env_file=None,
            settings_environment={
                "DEFAULT_RUN_CONCURRENCY": "20",
                "MAX_RUN_CONCURRENCY": "10",
            },
        )


def test_environment_remains_authoritative_after_database_update_attempt() -> None:
    repository = InMemorySettingsRepository({"run_retention_days": "30"})
    app = create_app(
        env_file=None,
        settings_repository=repository,
        settings_environment={"RUN_RETENTION_DAYS": "7"},
    )
    service = app.extensions["settings_service"]

    with pytest.raises(ConfigurationError, match="read-only"):
        service.set_database_value("run_retention_days", "14")

    resolved = app.extensions["settings"]
    assert resolved.values.run_retention_days == 7
    assert resolved.get("run_retention_days").source is SettingSource.ENVIRONMENT
    assert repository.get("run_retention_days") == "30"


def test_database_setting_survives_app_restart(tmp_path: Path) -> None:
    database = tmp_path / "persistent.db"
    environment = {
        "DATABASE_PATH": str(database),
        "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    first_app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    first_app.extensions["settings_service"].set_database_value("run_retention_days", "45")

    second_app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )

    resolved = second_app.extensions["settings"]
    assert resolved.values.run_retention_days == 45
    assert resolved.get("run_retention_days").source is SettingSource.DATABASE


def test_app_uses_database_setting_when_environment_is_absent(tmp_path: Path) -> None:
    repository = InMemorySettingsRepository({"port": "5100", "run_retention_days": "90"})

    app = create_app(
        env_file=None,
        settings_repository=repository,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "unused.db"),
            "EXPORT_PATH": str(tmp_path / "unused-exports"),
        },
    )

    resolved = app.extensions["settings"]
    assert resolved.values.port == 5100
    assert resolved.values.run_retention_days == 90
    assert resolved.get("port").source is SettingSource.DATABASE
    assert not (tmp_path / "unused.db").exists()
    assert not (tmp_path / "unused-exports").exists()
