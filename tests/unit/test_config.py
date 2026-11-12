from __future__ import annotations

import os
from pathlib import Path

import pytest

from evaluator.config import ConfigurationError, load_bootstrap_settings

pytestmark = pytest.mark.unit

_TEST_SECRET_KEY = "test-secret-key-with-at-least-32-bytes"


def _environment(**values: str) -> dict[str, str]:
    return {"SECRET_KEY": _TEST_SECRET_KEY, **values}


def test_missing_secret_key_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="SECRET_KEY.*at least 32 bytes"):
        load_bootstrap_settings(env_file=None, environment={})


def test_defaults_are_used_with_explicit_secret_key() -> None:
    settings = load_bootstrap_settings(env_file=None, environment=_environment())

    assert settings.host == "127.0.0.1"
    assert settings.port == 5000
    assert settings.app_process_count == 1
    assert settings.storage_backend == "local"
    assert settings.workers_enabled is True
    assert settings.reloader_enabled is False


def test_dotenv_overrides_existing_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SECRET_KEY={_TEST_SECRET_KEY}\nPORT=5123\nLOG_LEVEL=DEBUG\n", encoding="utf-8"
    )
    monkeypatch.setenv("PORT", "5999")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    settings = load_bootstrap_settings(env_file)

    assert settings.port == 5123
    assert settings.log_level == "DEBUG"
    assert os.environ["PORT"] == "5123"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORT", "not-a-number"),
        ("PORT", "70000"),
        ("WORKERS_ENABLED", "not-a-boolean"),
        ("APP_PROCESS_COUNT", "0"),
    ],
)
def test_invalid_values_raise_configuration_error(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        load_bootstrap_settings(env_file=None, environment=_environment(**{name: value}))


@pytest.mark.parametrize("process_count", ["2", "4"])
def test_multiple_processes_are_rejected(process_count: str) -> None:
    with pytest.raises(ConfigurationError, match="app_process_count must be 1"):
        load_bootstrap_settings(
            env_file=None, environment=_environment(APP_PROCESS_COUNT=process_count)
        )


def test_network_filesystem_backend_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="network filesystems are unsupported"):
        load_bootstrap_settings(env_file=None, environment=_environment(STORAGE_BACKEND="nfs"))


def test_reloader_is_rejected_when_workers_are_enabled() -> None:
    with pytest.raises(ConfigurationError, match="reloader_enabled must be false"):
        load_bootstrap_settings(
            env_file=None,
            environment=_environment(WORKERS_ENABLED="true", RELOADER_ENABLED="true"),
        )


def test_reloader_is_allowed_without_workers() -> None:
    settings = load_bootstrap_settings(
        env_file=None,
        environment=_environment(WORKERS_ENABLED="false", RELOADER_ENABLED="true"),
    )

    assert settings.reloader_enabled is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_host_is_rejected(host: str) -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        load_bootstrap_settings(env_file=None, environment=_environment(HOST=host))


@pytest.mark.parametrize(
    "weak_key", ["change-me", "change-me-local", "replace-with-a-long-random-value", "short"]
)
def test_weak_secret_keys_are_rejected_without_echoing_value(weak_key: str) -> None:
    with pytest.raises(ConfigurationError) as captured:
        load_bootstrap_settings(env_file=None, environment={"SECRET_KEY": weak_key})

    message = str(captured.value)
    assert "SECRET_KEY" in message
    assert weak_key not in message


def test_secret_key_accepts_at_least_32_utf8_bytes() -> None:
    value = "é" * 16
    settings = load_bootstrap_settings(env_file=None, environment={"SECRET_KEY": value})

    assert settings.secret_key == value
