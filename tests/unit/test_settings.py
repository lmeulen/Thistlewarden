from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.config import ConfigurationError
from evaluator.settings import (
    SETTINGS_REGISTRY,
    ApplicationSettings,
    InMemorySettingsRepository,
    SettingSource,
    SettingsResolver,
    SettingsService,
)

pytestmark = pytest.mark.unit


def test_registry_contains_every_application_setting() -> None:
    assert set(SETTINGS_REGISTRY) == set(ApplicationSettings.model_fields)
    assert all(definition.env_name for definition in SETTINGS_REGISTRY.values())
    assert all(definition.value_type for definition in SETTINGS_REGISTRY.values())


def test_every_setting_resolves_to_registered_default() -> None:
    resolved = SettingsResolver(InMemorySettingsRepository()).resolve({})

    for name, definition in SETTINGS_REGISTRY.items():
        item = resolved.get(name)
        assert item.value == definition.default
        assert item.source is SettingSource.DEFAULT
        assert item.read_only is False


def test_database_value_overrides_default() -> None:
    repository = InMemorySettingsRepository({"port": "5100"})

    resolved = SettingsResolver(repository).resolve({})

    assert resolved.values.port == 5100
    assert resolved.get("port").source is SettingSource.DATABASE


def test_environment_value_overrides_database() -> None:
    repository = InMemorySettingsRepository({"port": "5100"})

    resolved = SettingsResolver(repository).resolve({"PORT": "5200"})

    assert resolved.values.port == 5200
    assert resolved.get("port").source is SettingSource.ENVIRONMENT
    assert resolved.get("port").read_only is True


def test_environment_override_is_read_only_for_database_service() -> None:
    repository = InMemorySettingsRepository({"port": "5100"})
    service = SettingsService(repository, {"PORT": "5200"})

    with pytest.raises(ConfigurationError, match="read-only"):
        service.set_database_value("port", "5300")

    assert repository.get("port") == "5100"


def test_valid_database_update_is_persisted_and_resolved() -> None:
    repository = InMemorySettingsRepository()
    service = SettingsService(repository, {})

    resolved = service.set_database_value("run_retention_days", "30")

    assert repository.get("run_retention_days") == "30"
    assert resolved.values.run_retention_days == 30
    assert resolved.get("run_retention_days").source is SettingSource.DATABASE


def test_invalid_database_update_is_not_persisted() -> None:
    repository = InMemorySettingsRepository({"max_run_concurrency": "10"})
    service = SettingsService(repository, {})

    with pytest.raises(ConfigurationError):
        service.set_database_value("max_run_concurrency", "0")

    assert repository.get("max_run_concurrency") == "10"


def test_default_concurrency_must_not_exceed_maximum() -> None:
    repository = InMemorySettingsRepository(
        {"default_run_concurrency": "11", "max_run_concurrency": "10"}
    )

    with pytest.raises(ConfigurationError, match="default_run_concurrency"):
        SettingsResolver(repository).resolve({})


def test_active_run_concurrency_must_not_exceed_maximum() -> None:
    with pytest.raises(ConfigurationError, match="run_concurrency"):
        SettingsResolver(InMemorySettingsRepository()).resolve(
            {"RUN_CONCURRENCY": "11", "MAX_RUN_CONCURRENCY": "10"}
        )


@pytest.mark.parametrize(
    "name",
    [
        "default_request_timeout_seconds",
        "run_concurrency",
        "default_run_concurrency",
        "max_run_concurrency",
        "max_prompt_chars",
        "max_system_prompt_chars",
        "max_expected_answer_chars",
        "max_model_answer_chars",
        "max_judge_rubric_context_chars",
        "max_import_file_bytes",
        "max_provider_response_bytes",
        "max_golden_set_items",
        "max_model_profiles_per_run",
    ],
)
def test_positive_limits_reject_zero(name: str) -> None:
    repository = InMemorySettingsRepository({name: "0"})

    with pytest.raises(ConfigurationError):
        SettingsResolver(repository).resolve({})


def test_zero_retention_means_unlimited() -> None:
    resolved = SettingsResolver(InMemorySettingsRepository()).resolve({"RUN_RETENTION_DAYS": "0"})

    assert resolved.values.run_retention_days == 0


def test_secret_metadata_never_returns_value() -> None:
    resolved = SettingsResolver(InMemorySettingsRepository()).resolve(
        {"ENCRYPTION_KEY": "fixture-secret"}
    )

    item = resolved.get("encryption_key")
    assert item.definition.secret is True
    assert item.public_value() is None


def test_path_values_are_typed() -> None:
    resolved = SettingsResolver(InMemorySettingsRepository()).resolve(
        {"DATABASE_PATH": "custom/evaluator.db"}
    )

    assert resolved.values.database_path == Path("custom/evaluator.db")


def test_delete_restores_default() -> None:
    repository = InMemorySettingsRepository({"port": "5100"})
    service = SettingsService(repository, {})

    resolved = service.delete_database_value("port")

    assert repository.get("port") is None
    assert resolved.values.port == 5000
    assert resolved.get("port").source is SettingSource.DEFAULT


def test_unknown_setting_is_rejected() -> None:
    service = SettingsService(InMemorySettingsRepository(), {})

    with pytest.raises(KeyError, match="Unknown setting"):
        service.set_database_value("does_not_exist", "1")
