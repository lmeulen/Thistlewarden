"""Bootstrap configuration loaded before database access exists."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evaluator.errors import ConfigurationError

SUPPORTED_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
SECRET_KEY_PLACEHOLDER = "replace-with-at-least-32-random-bytes"
_WEAK_SECRET_KEYS = frozenset(
    {
        "",
        "change-me",
        "change-me-local",
        "replace-with-a-long-random-value",
        SECRET_KEY_PLACEHOLDER,
    }
)


def validate_secret_key(value: str) -> str:
    """Reject predictable or undersized Flask signing keys without exposing value."""

    if value in _WEAK_SECRET_KEYS or len(value.encode("utf-8")) < 32:
        raise ValueError("SECRET_KEY must be a non-default random value of at least 32 bytes")
    return value


class BootstrapSettings(BaseModel):
    """Settings needed to start one local application process safely."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flask_env: str = "development"
    secret_key: str = SECRET_KEY_PLACEHOLDER
    host: str = "127.0.0.1"
    port: int = Field(default=5000, ge=1, le=65535)
    log_level: str = "INFO"
    database_path: Path = Path("./data/evaluator.db")
    export_path: Path = Path("./exports")
    app_process_count: int = Field(default=1, ge=1)
    storage_backend: str = "local"
    workers_enabled: bool = True
    reloader_enabled: bool = False
    container_mode: bool = False

    @field_validator("secret_key")
    @classmethod
    def require_strong_secret_key(cls, value: str) -> str:
        return validate_secret_key(value)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        level = str(value).upper()
        if level not in SUPPORTED_LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(SUPPORTED_LOG_LEVELS)}")
        return level

    @field_validator("storage_backend")
    @classmethod
    def require_local_storage(cls, value: str) -> str:
        if value.lower() != "local":
            raise ValueError("storage_backend must be 'local'; network filesystems are unsupported")
        return "local"

    @model_validator(mode="after")
    def validate_runtime_topology(self) -> BootstrapSettings:
        allowed_hosts = {"127.0.0.1", "::1", "localhost"}
        if self.container_mode:
            allowed_hosts.add("0.0.0.0")
        if self.host not in allowed_hosts:
            raise ValueError("host must be loopback, or 0.0.0.0 only in container mode")
        if self.app_process_count != 1:
            raise ValueError("app_process_count must be 1 for the MVP runtime")
        if self.workers_enabled and self.reloader_enabled:
            raise ValueError("reloader_enabled must be false while workers are enabled")
        return self


def _env_value(environment: Mapping[str, str], name: str, default: object) -> object:
    return environment.get(name, default)


def load_bootstrap_settings(
    env_file: str | Path | None = ".env",
    *,
    environment: Mapping[str, str] | None = None,
) -> BootstrapSettings:
    """Load `.env` with override semantics, then validate bootstrap settings.

    Passing ``environment`` avoids global environment mutation and is intended for tests.
    Production loading follows the mandated python-dotenv ``override=True`` behavior.
    """

    if environment is None:
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=True)
        source: Mapping[str, str] = os.environ
    else:
        source = environment

    values = {
        "flask_env": _env_value(source, "FLASK_ENV", "development"),
        "secret_key": _env_value(source, "SECRET_KEY", SECRET_KEY_PLACEHOLDER),
        "host": _env_value(source, "HOST", "127.0.0.1"),
        "port": _env_value(source, "PORT", 5000),
        "log_level": _env_value(source, "LOG_LEVEL", "INFO"),
        "database_path": _env_value(source, "DATABASE_PATH", "./data/evaluator.db"),
        "export_path": _env_value(source, "EXPORT_PATH", "./exports"),
        "app_process_count": _env_value(source, "APP_PROCESS_COUNT", 1),
        "storage_backend": _env_value(source, "STORAGE_BACKEND", "local"),
        "workers_enabled": _env_value(source, "WORKERS_ENABLED", True),
        "reloader_enabled": _env_value(source, "RELOADER_ENABLED", False),
        "container_mode": _env_value(source, "CONTAINER_MODE", False),
    }
    try:
        return BootstrapSettings.model_validate(values)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_url=False, include_input=False)
        )
        raise ConfigurationError(f"Invalid bootstrap configuration: {details}") from exc
