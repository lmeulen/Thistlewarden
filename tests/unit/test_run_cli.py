from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from evaluator.api.errors import ValidationApiError
from evaluator.run_cli import (
    RunExitCode,
    build_summary,
    exit_code_for_summary,
    parse_gate_overrides,
    perform_exports,
    render_human_summary,
    render_json,
    wait_for_terminal,
    write_export,
)

pytestmark = pytest.mark.unit


class FakeView:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.index = 0

    def run(self, run_id: UUID) -> dict[str, Any]:
        status = self.statuses[min(self.index, len(self.statuses) - 1)]
        self.index += 1
        return {
            "id": str(run_id),
            "name": "Safe run",
            "status": status,
            "gate_status": "passed",
            "total_jobs": 2,
            "done_jobs": 2 if status == "completed" else 0,
        }

    def models(self, run_id: UUID) -> list[dict[str, Any]]:
        return [
            {
                "id": "snapshot-1",
                "display_name": "Model A",
                "model_id": "model-a",
            }
        ]


class FakeAggregates:
    def for_run(self, run_id: UUID) -> list[dict[str, Any]]:
        return [
            {
                "model_snapshot_id": "snapshot-1",
                "total": 2,
                "completed": 2,
                "passed": 1,
                "failed": 1,
                "errors": 0,
                "guardrails": 0,
                "provider_errors": 0,
                "processing_errors": 0,
                "unknown_errors": 0,
                "weighted_score": Decimal("0.5"),
                "total_cost": Decimal("0.01"),
            }
        ]


class FakeGates:
    def __init__(self, status: str = "passed") -> None:
        self.status = status

    def results(self, run_id: UUID) -> dict[str, Any]:
        return {"status": self.status, "gates": []}


class FakeExports:
    def export_json(self, run_id: UUID) -> bytes:
        return b"json"

    def export_csv(self, run_id: UUID) -> bytes:
        return b"csv"

    def export_html(self, run_id: UUID) -> bytes:
        return b"html"


def _summary(**updates: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "completed",
        "gate_status": "passed",
        "wait_outcome": "terminal",
        "jobs": {"errors": 0},
    }
    result.update(updates)
    return result


def test_gate_overrides_are_strict_and_support_explicit_empty() -> None:
    assert parse_gate_overrides({}, no_gates=False) is None
    assert parse_gate_overrides({}, no_gates=True) == {}
    assert parse_gate_overrides({"min_pass_rate": "0.9"}, no_gates=False) == {
        "min_pass_rate": "0.9"
    }
    with pytest.raises(ValidationApiError):
        parse_gate_overrides({"min_pass_rate": "2"}, no_gates=False)
    with pytest.raises(ValidationApiError):
        parse_gate_overrides({"min_pass_rate": "0.9"}, no_gates=True)


@pytest.mark.parametrize(
    ("updates", "policy", "expected"),
    [
        ({}, "fail", RunExitCode.SUCCESS),
        ({"gate_status": "failed"}, "fail", RunExitCode.GATE_FAILED),
        ({"gate_status": "indeterminate"}, "fail", RunExitCode.GATE_INDETERMINATE),
        ({"gate_status": "indeterminate"}, "pass", RunExitCode.SUCCESS),
        ({"status": "completed_with_errors"}, "fail", RunExitCode.EXECUTION_ERROR),
        ({"status": "failed"}, "fail", RunExitCode.EXECUTION_ERROR),
        ({"status": "cancelled"}, "fail", RunExitCode.CANCELLED),
        ({"wait_outcome": "interrupted"}, "fail", RunExitCode.INTERRUPTED),
        ({"wait_outcome": "timeout"}, "fail", RunExitCode.TIMEOUT),
    ],
)
def test_exit_code_contract(updates: dict[str, Any], policy: str, expected: RunExitCode) -> None:
    assert exit_code_for_summary(_summary(**updates), indeterminate_policy=policy) == expected


def test_wait_reaches_terminal_and_timeout_without_mutating_run() -> None:
    run_id = uuid4()
    view = FakeView(["pending", "running", "completed"])
    run, outcome = wait_for_terminal(
        run_id, view, timeout=10, poll_interval=0.1, sleep=lambda _: None
    )
    assert (run["status"], outcome) == ("completed", "terminal")

    times = iter([0.0, 0.0, 2.0])
    timed_view = FakeView(["running"])
    run, outcome = wait_for_terminal(
        run_id,
        timed_view,
        timeout=1,
        poll_interval=0.1,
        monotonic=lambda: next(times),
        sleep=lambda _: None,
    )
    assert (run["status"], outcome) == ("running", "timeout")


def test_summary_is_safe_and_machine_readable() -> None:
    run_id = uuid4()
    summary = build_summary(
        run_id,
        FakeView(["completed"]),
        FakeAggregates(),
        FakeGates(),
        wait_outcome="terminal",
    )
    assert summary["cost"] == {"configured": True, "total": "0.01"}
    assert summary["models"][0]["weighted_score"] == "0.5"
    rendered = render_json(summary)
    assert json.loads(rendered)["run_id"] == str(run_id)
    assert "prompt" not in rendered
    assert "answer" not in rendered
    human = render_human_summary(summary)
    assert f"Run: {run_id}" in human
    assert "Model Model A" in human


def test_exports_refuse_existing_files_unless_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_export(destination, b"first", overwrite=False)
    with pytest.raises(FileExistsError):
        write_export(destination, b"second", overwrite=False)
    assert destination.read_bytes() == b"first"
    write_export(destination, b"second", overwrite=True)
    assert destination.read_bytes() == b"second"


def test_multiple_exports_preflight_existing_destinations(tmp_path: Path) -> None:
    existing = tmp_path / "result.json"
    existing.write_text("keep", encoding="utf-8")
    csv_path = tmp_path / "result.csv"
    with pytest.raises(FileExistsError):
        perform_exports(
            FakeExports(),
            uuid4(),
            {"json": existing, "csv": csv_path, "html": None},
            overwrite=False,
        )
    assert not csv_path.exists()
