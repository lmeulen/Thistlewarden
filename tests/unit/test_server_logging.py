from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from evaluator.config import BootstrapSettings
from evaluator.server import run


class FakeRuntime:
    running_threads = 2

    def start(self) -> None:
        pass


class FakeServer:
    def run(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_production_server_logs_visible_lifecycle(
    monkeypatch: Any, caplog: Any, tmp_path: Path
) -> None:
    shutdown = []
    app = type(
        "App",
        (),
        {
            "extensions": {
                "worker_runtime": FakeRuntime(),
                "shutdown_services": lambda: shutdown.append(True),
            }
        },
    )()
    monkeypatch.setattr("evaluator.server.create_app", lambda **kwargs: app)
    monkeypatch.setattr("evaluator.server.create_server", lambda *args, **kwargs: FakeServer())
    monkeypatch.setattr("evaluator.server.InstanceLock.for_database", lambda path: _FakeLock())
    settings = BootstrapSettings(database_path=tmp_path / "test.db")

    with caplog.at_level(logging.INFO, logger="evaluator.server"):
        run(settings)

    messages = [record.getMessage() for record in caplog.records]
    assert any("application_initialized" in message for message in messages)
    assert any("workers_started count=2" in message for message in messages)
    assert any("http_server_listening host=127.0.0.1 port=5000" in message for message in messages)
    assert any("shutdown_completed" in message for message in messages)
    assert shutdown == [True]


def test_production_server_logs_incomplete_shutdown(
    monkeypatch: Any, caplog: Any, tmp_path: Path
) -> None:
    app = type(
        "App",
        (),
        {
            "extensions": {
                "worker_runtime": FakeRuntime(),
                "shutdown_services": lambda: False,
            }
        },
    )()
    monkeypatch.setattr("evaluator.server.create_app", lambda **kwargs: app)
    monkeypatch.setattr("evaluator.server.create_server", lambda *args, **kwargs: FakeServer())
    monkeypatch.setattr("evaluator.server.InstanceLock.for_database", lambda path: _FakeLock())

    with caplog.at_level(logging.INFO, logger="evaluator.server"):
        run(BootstrapSettings(database_path=tmp_path / "test.db"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("shutdown_incomplete" in message for message in messages)
    assert not any("shutdown_completed" in message for message in messages)


class _FakeLock:
    def __enter__(self) -> _FakeLock:
        return self

    def __exit__(self, *args: object) -> None:
        pass
