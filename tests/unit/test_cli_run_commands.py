from __future__ import annotations

from argparse import Namespace
from typing import Any
from uuid import uuid4

import pytest

from evaluator import cli
from evaluator.run_cli import RunExitCode

pytestmark = pytest.mark.unit


class FakeRuntime:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 30.0) -> bool:
        self.stopped = True
        return True


class FakeView:
    def run(self, run_id: Any) -> dict[str, Any]:
        return {"status": "running"}


def test_wait_interrupt_stops_local_runtime_without_cancelling_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime()
    app = type(
        "FakeApp",
        (),
        {"extensions": {"worker_runtime": runtime, "run_view_service": FakeView()}},
    )()
    summary = {
        "schema_version": "1.0",
        "run_id": str(uuid4()),
        "status": "running",
        "gate_status": "not_evaluated",
        "wait_outcome": "interrupted",
        "jobs": {"total": 1, "completed": 0, "errors": 0},
        "models": [],
        "cost": {"configured": False, "total": None},
        "exports": [],
    }
    monkeypatch.setattr(
        cli, "wait_for_terminal", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    monkeypatch.setattr(cli, "_build_summary", lambda *args, **kwargs: summary)
    monkeypatch.setattr(cli, "_print_summary", lambda *args, **kwargs: None)
    args = Namespace(
        timeout=None,
        poll_interval=0.5,
        output="json",
        indeterminate="fail",
        overwrite=False,
        export_json=None,
        export_csv=None,
        export_html=None,
    )

    returned, code = cli._wait(app, uuid4(), args)

    assert runtime.started and runtime.stopped
    assert returned["status"] == "running"
    assert code == RunExitCode.INTERRUPTED
    assert "run_queue" not in app.extensions
