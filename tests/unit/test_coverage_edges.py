from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from evaluator import cli, container_entrypoint
from evaluator.api.errors import InvalidProviderResponseError, TimeoutError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.discovery import ModelDiscoveryClient
from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.errors import ConfigurationError
from evaluator.migrations import MigrationManager, SQLiteBackupService
from evaluator.mock_provider import _answer
from evaluator.run_cli import RunExitCode
from evaluator.run_exports import RunExportService, serialize_money
from evaluator.runtime import InstanceLock

pytestmark = pytest.mark.unit


class _Lock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


class _App:
    def __init__(self) -> None:
        self.stopped = False
        self.extensions: dict[str, Any] = {
            "database_factory": object(),
            "run_queue": SimpleNamespace(create_run=lambda payload: uuid4()),
            "shutdown_services": self.shutdown,
        }

    def shutdown(self) -> None:
        self.stopped = True


def _run_args(command: str, **values: object) -> Namespace:
    defaults: dict[str, object] = {
        "env_file": None,
        "run_command": command,
        "output": "json",
        "kind": "models",
        "name": None,
        "golden_set": "set",
        "model": ["model"],
        "judge": None,
        "note": None,
        "no_gates": True,
        "wait": False,
        "run_id": uuid4(),
        "timeout": None,
        "poll_interval": 0.01,
        "indeterminate": "fail",
        "overwrite": False,
        "export_json": None,
        "export_csv": None,
        "export_html": None,
    }
    defaults.update(values)
    return Namespace(**defaults)


def _patch_run_app(monkeypatch: pytest.MonkeyPatch, app: _App) -> None:
    monkeypatch.setattr(
        cli,
        "load_bootstrap_settings",
        lambda path: SimpleNamespace(database_path=Path("x")),
    )
    monkeypatch.setattr(InstanceLock, "for_database", lambda path: _Lock())
    monkeypatch.setattr(cli, "create_app", lambda **kwargs: app)


def test_run_command_lookup_start_show_wait_and_export(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _App()
    _patch_run_app(monkeypatch, app)
    monkeypatch.setattr(cli, "lookup_records", lambda factory, kind: [{"id": "1", "name": "M"}])
    monkeypatch.setattr(cli, "resolve_selector", lambda factory, kind, value: uuid4())
    monkeypatch.setattr(cli, "parse_gate_overrides", lambda values, no_gates: None)
    monkeypatch.setattr(cli, "_build_summary", lambda *args: {"status": "pending"})
    monkeypatch.setattr(cli, "_wait", lambda *args: ({}, RunExitCode.SUCCESS))
    monkeypatch.setattr(cli, "_export", lambda *args: ["report.json"])

    assert cli._run_command(_run_args("lookup")) == RunExitCode.SUCCESS
    assert (
        cli._run_command(_run_args("start", judge="judge", no_gates=False)) == RunExitCode.SUCCESS
    )
    assert cli._run_command(_run_args("start", wait=True)) == RunExitCode.SUCCESS
    assert cli._run_command(_run_args("show")) == RunExitCode.SUCCESS
    assert cli._run_command(_run_args("wait")) == RunExitCode.SUCCESS
    assert cli._run_command(_run_args("export")) == RunExitCode.SUCCESS
    assert '"exports":["report.json"]' in capsys.readouterr().out
    assert app.stopped


def test_run_command_export_requires_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _App()
    _patch_run_app(monkeypatch, app)
    monkeypatch.setattr(cli, "_export", lambda *args: [])
    with pytest.raises(ValidationApiError, match="at least one"):
        cli._run_command(_run_args("export"))
    assert app.stopped


def test_cli_helpers_cover_empty_human_output_and_missing_extension(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_lookup([], "human")
    assert capsys.readouterr().out == "No active records found.\n"
    cli._print_lookup([{"id": "stable-id", "name": "Model", "optional": None}], "human")
    assert capsys.readouterr().out == "stable-id  Model\n"
    with pytest.raises(Exception, match="Required run service"):
        cli._extension(SimpleNamespace(extensions={}), "missing")
    args = Namespace(export_json=Path("a"), export_csv=None)
    assert cli._destinations(args) == {"json": Path("a"), "csv": None, "html": None}


def test_cli_main_admin_dispatch_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(log_level="WARNING", database_path=tmp_path / "db.sqlite")
    monkeypatch.setattr(cli, "_settings", lambda path: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(cli, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(MigrationManager, "migrate", lambda self: None)
    monkeypatch.setattr(SQLiteBackupService, "backup", lambda self, destination: None)
    monkeypatch.setattr(SQLiteBackupService, "restore", lambda source, destination: None)

    assert cli.main(["--env-file", str(tmp_path / "none"), "migrate"]) == 0
    assert cli.main(["backup", str(tmp_path / "backup")]) == 0
    assert cli.main(["restore", str(tmp_path / "backup"), str(tmp_path / "restored")]) == 0

    monkeypatch.setattr(cli, "_run_command", lambda args: (_ for _ in ()).throw(ValueError("bad")))
    assert cli.main(["run", "lookup", "models"]) == RunExitCode.USAGE
    assert "Error: bad" in capsys.readouterr().err

    monkeypatch.setattr(
        cli, "_run_command", lambda args: (_ for _ in ()).throw(OSError("write failed"))
    )
    assert cli.main(["run", "lookup", "models"]) == RunExitCode.EXPORT_ERROR
    monkeypatch.setattr(
        cli,
        "_run_command",
        lambda args: (_ for _ in ()).throw(ConfigurationError("runtime failed")),
    )
    assert cli.main(["run", "lookup", "models"]) == RunExitCode.RUNTIME_ERROR


def test_database_guards_and_noncreating_connection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        SQLiteConnectionFactory(tmp_path / "x", busy_timeout_ms=0)
    factory = SQLiteConnectionFactory(tmp_path / "present" / "db.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        factory.connect(create_parent=False)
    connection = SQLiteConnectionFactory(tmp_path / "db.sqlite").connect()
    with pytest.raises(ValueError, match="unsupported"):
        with transaction(connection, mode="wrong"):
            pass
    connection.execute("BEGIN")
    with pytest.raises(RuntimeError, match="nested"):
        with transaction(connection):
            pass
    connection.rollback()
    connection.close()


def test_container_entrypoint_validates_mode_and_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        container_mode=False,
        database_path=tmp_path / "data" / "db.sqlite",
        export_path=tmp_path / "exports",
    )
    monkeypatch.setattr(container_entrypoint, "load_bootstrap_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="CONTAINER_MODE"):
        container_entrypoint.main()

    called: list[object] = []
    settings.container_mode = True
    monkeypatch.setattr(container_entrypoint, "run", called.append)
    container_entrypoint.main()
    assert called == [settings]
    assert settings.database_path.parent.is_dir() and settings.export_path.is_dir()
    assert not list(tmp_path.rglob(".write-probe"))


def test_mock_answers_cover_judge_markers_and_default() -> None:
    prompt = '<evaluation-data>\n{"rubric":[{"id":"a"}]}\n</evaluation-data>'
    judged = json.loads(_answer(prompt, "m"))
    assert judged["criteria"] == [{"id": "a", "score": "1", "reason": "Mock criterion passed."}]
    assert _answer("[EXACT]", "m") == "exact antwoord"
    assert _answer("[JSON]", "model-x") == '{"ok":true,"model":"model-x"}'
    assert _answer("other", "m") == "mock answer"


def test_wait_exports_terminal_result_and_reports_export_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SimpleNamespace(start=lambda: None, stop=lambda timeout: True)
    app = SimpleNamespace(
        extensions={"worker_runtime": runtime, "run_view_service": SimpleNamespace()}
    )
    args = _run_args("wait")
    monkeypatch.setattr(cli, "wait_for_terminal", lambda *args, **kwargs: ({}, "terminal"))
    monkeypatch.setattr(
        cli,
        "_build_summary",
        lambda *args: {
            "status": "completed",
            "wait_outcome": "terminal",
            "jobs": {"errors": 0},
            "gate_status": "passed",
        },
    )
    monkeypatch.setattr(cli, "_print_summary", lambda *args: None)
    monkeypatch.setattr(cli, "_export", lambda *args: ["run.json"])
    summary, code = cli._wait(app, uuid4(), args)
    assert summary["exports"] == ["run.json"] and code == RunExitCode.SUCCESS

    monkeypatch.setattr(cli, "_export", lambda *args: (_ for _ in ()).throw(OSError("locked")))
    summary, code = cli._wait(app, uuid4(), args)
    assert summary["export_error"] == "locked" and code == RunExitCode.EXPORT_ERROR


def test_model_discovery_client_contract_timeout_and_size_limit() -> None:
    endpoint = EndpointRecord(
        uuid4(),
        EndpointInput(
            name="Provider",
            completions_url="https://provider.test/v1/chat/completions",
            models_url="https://provider.test/v1/models",
        ),
        None,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    headers = SimpleNamespace(resolve=lambda endpoint: {"Authorization": "Bearer safe"})
    good = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "model-a"}]})
    )
    assert ModelDiscoveryClient(headers, max_response_bytes=1000, transport=good).discover(
        endpoint
    ) == ["model-a"]
    oversized = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 20))
    with pytest.raises(InvalidProviderResponseError, match="maximum size"):
        ModelDiscoveryClient(headers, max_response_bytes=5, transport=oversized).discover(endpoint)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    with pytest.raises(TimeoutError):
        ModelDiscoveryClient(
            headers, max_response_bytes=100, transport=httpx.MockTransport(timeout)
        ).discover(endpoint)

    without_url = EndpointRecord(
        endpoint.id,
        EndpointInput(
            name="No models", completions_url="https://provider.test/v1/chat/completions"
        ),
        None,
        endpoint.created_at,
        endpoint.updated_at,
    )
    with pytest.raises(ValidationApiError, match="Models URL"):
        ModelDiscoveryClient(headers, max_response_bytes=100, transport=good).discover(without_url)


def test_run_export_validation_serialization_and_missing_run(tmp_path: Path) -> None:
    service = RunExportService(SQLiteConnectionFactory(tmp_path / "empty.sqlite"))
    with pytest.raises(ValidationApiError, match="at least one"):
        service.delete_many([])
    with pytest.raises(ValueError, match="non-negative"):
        service.cleanup(-1)
    assert service.cleanup(0) == 0
    assert serialize_money(None) is None
    from decimal import Decimal

    assert serialize_money(Decimal("1.2")) == "1.20000000"
