"""Typed operational settings with layered source resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evaluator.errors import ConfigurationError
from evaluator.logging_config import log_semantic_event

_LOG = logging.getLogger(__name__)

SUPPORTED_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

type SettingValue = str | int | bool | Path
type SettingType = type[str] | type[int] | type[bool] | type[Path]


class SettingSource(StrEnum):
    """Source for a resolved setting."""

    DEFAULT = "default"
    DATABASE = "database"
    ENVIRONMENT = "environment"


class SettingLifecycle(StrEnum):
    """When a validated setting can become active."""

    BOOTSTRAP = "bootstrap"
    RESTART_REQUIRED = "restart_required"
    DYNAMIC = "dynamic"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    """One authoritative operational setting definition."""

    name: str
    env_name: str
    value_type: SettingType
    default: SettingValue
    lifecycle: SettingLifecycle
    minimum: int | None = None
    maximum: int | None = None
    secret: bool = False


class SettingsRepository(Protocol):
    """Persistence port used by layered settings resolution."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def all(self) -> Mapping[str, str]: ...


class InMemorySettingsRepository:
    """Temporary settings adapter used until SQLite is introduced in step 3."""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._values = dict(initial or {})

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)

    def all(self) -> Mapping[str, str]:
        return dict(self._values)


class ApplicationSettings(BaseModel):
    """Validated values after default/database/environment resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flask_env: str = "development"
    secret_key: str = "change-me"
    encryption_key: str = "change-me"
    host: str = "127.0.0.1"
    port: int = Field(default=5000, ge=1, le=65535)
    log_level: str = "INFO"
    database_path: Path = Path("./data/evaluator.db")
    export_path: Path = Path("./exports")
    app_process_count: int = Field(default=1, ge=1, le=1)
    storage_backend: str = "local"
    workers_enabled: bool = True
    reloader_enabled: bool = False
    container_mode: bool = False
    default_request_timeout_seconds: int = Field(default=60, gt=0)
    default_max_retries: int = Field(default=2, ge=0)
    run_concurrency: int = Field(default=4, gt=0)
    default_run_concurrency: int = Field(default=4, gt=0)
    max_run_concurrency: int = Field(default=10, gt=0)
    run_retention_days: int = Field(default=0, ge=0)
    max_prompt_chars: int = Field(default=1_000_000, gt=0)
    max_system_prompt_chars: int = Field(default=100_000, gt=0)
    max_expected_answer_chars: int = Field(default=1_000_000, gt=0)
    max_model_answer_chars: int = Field(default=10_000_000, gt=0)
    max_judge_rubric_context_chars: int = Field(default=100_000, gt=0)
    max_import_file_bytes: int = Field(default=104_857_600, gt=0)
    max_provider_response_bytes: int = Field(default=104_857_600, gt=0)
    max_golden_set_items: int = Field(default=10_000, gt=0)
    max_model_profiles_per_run: int = Field(default=20, gt=0)

    @model_validator(mode="after")
    def validate_combinations(self) -> ApplicationSettings:
        allowed_hosts = {"127.0.0.1", "::1", "localhost"}
        if self.container_mode:
            allowed_hosts.add("0.0.0.0")
        if self.host not in allowed_hosts:
            raise ValueError("host must be loopback, or 0.0.0.0 only in container mode")
        if self.log_level.upper() not in SUPPORTED_LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(SUPPORTED_LOG_LEVELS)}")
        if self.storage_backend.lower() != "local":
            raise ValueError("storage_backend must be 'local'; network filesystems are unsupported")
        if self.workers_enabled and self.reloader_enabled:
            raise ValueError("reloader_enabled must be false while workers are enabled")
        if self.default_run_concurrency > self.max_run_concurrency:
            raise ValueError("default_run_concurrency must not exceed max_run_concurrency")
        if self.run_concurrency > self.max_run_concurrency:
            raise ValueError("run_concurrency must not exceed max_run_concurrency")
        return self


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    """A validated setting value plus provenance metadata."""

    definition: SettingDefinition
    value: SettingValue
    source: SettingSource

    @property
    def read_only(self) -> bool:
        return (
            self.definition.lifecycle is SettingLifecycle.BOOTSTRAP
            or self.source is SettingSource.ENVIRONMENT
        )

    def public_value(self) -> SettingValue | None:
        return None if self.definition.secret else self.value


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    """Complete application settings and metadata indexed by field name."""

    values: ApplicationSettings
    metadata: Mapping[str, ResolvedSetting]

    def get(self, name: str) -> ResolvedSetting:
        try:
            return self.metadata[name]
        except KeyError as exc:
            raise KeyError(f"Unknown setting: {name}") from exc


# Defaults, validation bounds, and lifecycle are defined exactly once here.
_B = SettingLifecycle.BOOTSTRAP
_R = SettingLifecycle.RESTART_REQUIRED
_D = SettingLifecycle.DYNAMIC
SETTINGS_REGISTRY: dict[str, SettingDefinition] = {
    definition.name: definition
    for definition in (
        SettingDefinition("flask_env", "FLASK_ENV", str, "development", _B),
        SettingDefinition("secret_key", "SECRET_KEY", str, "change-me", _B, secret=True),
        SettingDefinition("encryption_key", "ENCRYPTION_KEY", str, "change-me", _B, secret=True),
        SettingDefinition("host", "HOST", str, "127.0.0.1", _B),
        SettingDefinition("port", "PORT", int, 5000, _B, 1, 65535),
        SettingDefinition("log_level", "LOG_LEVEL", str, "INFO", _D),
        SettingDefinition("database_path", "DATABASE_PATH", Path, Path("./data/evaluator.db"), _B),
        SettingDefinition("export_path", "EXPORT_PATH", Path, Path("./exports"), _B),
        SettingDefinition("app_process_count", "APP_PROCESS_COUNT", int, 1, _B, 1, 1),
        SettingDefinition("storage_backend", "STORAGE_BACKEND", str, "local", _B),
        SettingDefinition("workers_enabled", "WORKERS_ENABLED", bool, True, _B),
        SettingDefinition("reloader_enabled", "RELOADER_ENABLED", bool, False, _B),
        SettingDefinition("container_mode", "CONTAINER_MODE", bool, False, _B),
        SettingDefinition(
            "default_request_timeout_seconds", "DEFAULT_REQUEST_TIMEOUT_SECONDS", int, 60, _R, 1
        ),
        SettingDefinition("default_max_retries", "DEFAULT_MAX_RETRIES", int, 2, _R, 0),
        SettingDefinition("run_concurrency", "RUN_CONCURRENCY", int, 4, _R, 1),
        SettingDefinition("default_run_concurrency", "DEFAULT_RUN_CONCURRENCY", int, 4, _R, 1),
        SettingDefinition("max_run_concurrency", "MAX_RUN_CONCURRENCY", int, 10, _R, 1),
        SettingDefinition("run_retention_days", "RUN_RETENTION_DAYS", int, 0, _R, 0),
        SettingDefinition("max_prompt_chars", "MAX_PROMPT_CHARS", int, 1_000_000, _R, 1),
        SettingDefinition(
            "max_system_prompt_chars", "MAX_SYSTEM_PROMPT_CHARS", int, 100_000, _R, 1
        ),
        SettingDefinition(
            "max_expected_answer_chars", "MAX_EXPECTED_ANSWER_CHARS", int, 1_000_000, _R, 1
        ),
        SettingDefinition(
            "max_model_answer_chars", "MAX_MODEL_ANSWER_CHARS", int, 10_000_000, _D, 1
        ),
        SettingDefinition(
            "max_judge_rubric_context_chars",
            "MAX_JUDGE_RUBRIC_CONTEXT_CHARS",
            int,
            100_000,
            _R,
            1,
        ),
        SettingDefinition(
            "max_import_file_bytes", "MAX_IMPORT_FILE_BYTES", int, 104_857_600, _R, 1
        ),
        SettingDefinition(
            "max_provider_response_bytes",
            "MAX_PROVIDER_RESPONSE_BYTES",
            int,
            104_857_600,
            _R,
            1,
        ),
        SettingDefinition("max_golden_set_items", "MAX_GOLDEN_SET_ITEMS", int, 10_000, _R, 1),
        SettingDefinition(
            "max_model_profiles_per_run", "MAX_MODEL_PROFILES_PER_RUN", int, 20, _R, 1
        ),
    )
}


def _parse_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("must be a boolean")


def _parse_value(definition: SettingDefinition, raw: object) -> SettingValue:
    if definition.value_type is bool:
        value: SettingValue = _parse_bool(raw)
    elif definition.value_type is int:
        value = int(str(raw))
    elif definition.value_type is Path:
        value = Path(str(raw))
    else:
        value = str(raw)

    if isinstance(value, int) and not isinstance(value, bool):
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(f"must be at least {definition.minimum}")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"must be at most {definition.maximum}")
    return value


class SettingsResolver:
    """Resolve settings using environment > repository > default priority."""

    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    def resolve(self, environment: Mapping[str, str]) -> ResolvedSettings:
        parsed: dict[str, SettingValue] = {}
        metadata: dict[str, ResolvedSetting] = {}
        errors: list[str] = []

        for name, definition in SETTINGS_REGISTRY.items():
            database_value = (
                None
                if definition.lifecycle is SettingLifecycle.BOOTSTRAP
                else self._repository.get(name)
            )
            if definition.env_name in environment:
                raw: object = environment[definition.env_name]
                source = SettingSource.ENVIRONMENT
            elif database_value is not None:
                raw = database_value
                source = SettingSource.DATABASE
            else:
                raw = definition.default
                source = SettingSource.DEFAULT
            try:
                value = _parse_value(definition, raw)
            except (TypeError, ValueError) as exc:
                errors.append(f"{definition.env_name}: {exc}")
                continue
            parsed[name] = value
            metadata[name] = ResolvedSetting(definition, value, source)

        if errors:
            raise ConfigurationError("Invalid settings: " + "; ".join(errors))
        try:
            values = ApplicationSettings.model_validate(parsed)
        except ValidationError as exc:
            raise ConfigurationError(f"Invalid settings combination: {exc}") from exc
        return ResolvedSettings(values=values, metadata=metadata)


class SettingsService:
    """Manage desired settings and explicitly apply dynamic changes."""

    def __init__(
        self,
        repository: SettingsRepository,
        environment: Mapping[str, str],
        active: ResolvedSettings | None = None,
        dynamic_appliers: Mapping[str, Callable[[SettingValue], None]] | None = None,
    ) -> None:
        self._repository = repository
        self._environment = environment
        self._active = active or SettingsResolver(repository).resolve(environment)
        self._desired = self._active
        self._dynamic_appliers = dict(dynamic_appliers or {})

    @property
    def active(self) -> ResolvedSettings:
        return self._active

    @property
    def desired(self) -> ResolvedSettings:
        return self._desired

    def _require_mutable(self, name: str) -> SettingDefinition:
        definition = SETTINGS_REGISTRY.get(name)
        if definition is None:
            raise KeyError(f"Unknown setting: {name}")
        if definition.lifecycle is SettingLifecycle.BOOTSTRAP:
            raise ConfigurationError(
                f"{definition.env_name} is a bootstrap setting and cannot be stored in the database"
            )
        if definition.env_name in self._environment:
            raise ConfigurationError(
                f"{definition.env_name} is controlled by the environment and is read-only"
            )
        return definition

    def _commit_resolution(
        self, definition: SettingDefinition, resolved: ResolvedSettings
    ) -> ResolvedSettings:
        if definition.lifecycle is SettingLifecycle.DYNAMIC:
            applier = self._dynamic_appliers.get(definition.name)
            if applier is None:
                raise RuntimeError(f"No dynamic applier registered for {definition.name}")
            desired_item = resolved.get(definition.name)
            applier(desired_item.value)
            active_metadata = dict(self._active.metadata)
            active_metadata[definition.name] = desired_item
            self._active = ResolvedSettings(
                values=self._active.values.model_copy(update={definition.name: desired_item.value}),
                metadata=active_metadata,
            )
        self._desired = resolved
        return resolved

    def set_database_value(self, name: str, raw_value: str) -> ResolvedSettings:
        definition = self._require_mutable(name)
        # Resolve against a temporary copy first, so invalid writes are atomic.
        candidate = InMemorySettingsRepository(self._repository.all())
        candidate.set(name, raw_value)
        resolved = SettingsResolver(candidate).resolve(self._environment)
        self._commit_resolution(definition, resolved)
        self._repository.set(name, raw_value)
        log_semantic_event(_LOG, "setting_updated", setting=name, source="database")
        return resolved

    def delete_database_value(self, name: str) -> ResolvedSettings:
        definition = self._require_mutable(name)
        candidate = InMemorySettingsRepository(self._repository.all())
        candidate.delete(name)
        resolved = SettingsResolver(candidate).resolve(self._environment)
        self._commit_resolution(definition, resolved)
        self._repository.delete(name)
        log_semantic_event(_LOG, "setting_updated", setting=name, source="default")
        return resolved
