from __future__ import annotations

import logging
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.api.errors import ValidationApiError
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


def test_app_ignores_bootstrap_database_setting_when_environment_is_absent(
    tmp_path: Path,
) -> None:
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
    assert resolved.values.port == 5000
    assert resolved.values.run_retention_days == 90
    assert resolved.get("port").source is SettingSource.DEFAULT
    assert not (tmp_path / "unused.db").exists()
    assert not (tmp_path / "unused-exports").exists()


def test_bootstrap_setting_update_is_rejected_by_running_app() -> None:
    repository = InMemorySettingsRepository()
    app = create_app(env_file=None, settings_repository=repository, settings_environment={})

    with pytest.raises(ConfigurationError, match="bootstrap setting"):
        app.extensions["settings_service"].set_database_value("workers_enabled", "false")

    assert repository.get("workers_enabled") is None


def test_restart_required_update_remains_desired_until_restart(tmp_path: Path) -> None:
    environment = {
        "DATABASE_PATH": str(tmp_path / "restart-required.db"),
        "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    first_app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    service = first_app.extensions["settings_service"]

    service.set_database_value("max_model_profiles_per_run", "12")

    assert service.active.values.max_model_profiles_per_run == 20
    assert service.desired.values.max_model_profiles_per_run == 12
    assert first_app.extensions["run_queue"]._max_profiles == 20

    restarted_app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    assert restarted_app.extensions["active_settings"].values.max_model_profiles_per_run == 12
    assert restarted_app.extensions["run_queue"]._max_profiles == 12


def test_dynamic_settings_apply_to_running_services() -> None:
    app = create_app(env_file=None, settings_environment={})
    service = app.extensions["settings_service"]

    service.set_database_value("log_level", "DEBUG")
    service.set_database_value("max_model_answer_chars", "3")

    assert logging.getLogger().level == logging.DEBUG
    assert service.active.values.log_level == "DEBUG"
    assert service.active.values.max_model_answer_chars == 3
    with pytest.raises(ValidationApiError, match="maximum length"):
        app.extensions["evaluation_preview_service"].evaluate(
            method="exact_match",
            config={"expected_value": "long"},
            expected_answer=None,
            candidate="long",
        )
