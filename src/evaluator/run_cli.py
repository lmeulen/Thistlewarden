"""Automation-oriented local run CLI contracts and orchestration."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from evaluator.aggregates import aggregate_to_dict
from evaluator.api.errors import ValidationApiError
from evaluator.database import SQLiteConnectionFactory
from evaluator.gates import QualityGates
from evaluator.run_views import TERMINAL_STATUSES


class RunExitCode(IntEnum):
    SUCCESS = 0
    USAGE = 2
    GATE_FAILED = 3
    GATE_INDETERMINATE = 4
    EXECUTION_ERROR = 5
    CANCELLED = 6
    INTERRUPTED = 7
    TIMEOUT = 8
    EXPORT_ERROR = 9
    RUNTIME_ERROR = 10


class WorkerRuntimeProtocol(Protocol):
    def start(self) -> None: ...

    def stop(self, timeout: float = 30.0) -> bool: ...


class RunViewProtocol(Protocol):
    def run(self, run_id: UUID) -> dict[str, Any]: ...

    def models(self, run_id: UUID) -> list[dict[str, Any]]: ...


class AggregateProtocol(Protocol):
    def for_run(self, run_id: UUID) -> list[dict[str, Any]]: ...


class GateProtocol(Protocol):
    def results(self, run_id: UUID) -> dict[str, Any]: ...


class ExportProtocol(Protocol):
    def export_json(self, run_id: UUID) -> bytes: ...

    def export_csv(self, run_id: UUID) -> bytes: ...

    def export_html(self, run_id: UUID) -> bytes: ...


_GATE_NAMES = tuple(QualityGates.model_fields)


def parse_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationApiError(
            f"{label} must be a valid UUID or unambiguous exact name."
        ) from exc


def parse_gate_overrides(
    values: Mapping[str, str | None], *, no_gates: bool
) -> dict[str, Any] | None:
    configured = {name: value for name in _GATE_NAMES if (value := values.get(name)) is not None}
    if no_gates and configured:
        raise ValidationApiError("--no-gates cannot be combined with gate overrides.")
    if no_gates:
        return {}
    if not configured:
        return None
    try:
        gates = QualityGates.model_validate(configured)
    except Exception as exc:
        raise ValidationApiError("Quality gate overrides are invalid.") from exc
    return gates.model_dump(mode="json", exclude_none=True)


def resolve_selector(factory: SQLiteConnectionFactory, kind: str, selector: str) -> UUID:
    """Resolve active run input by UUID or unique case-insensitive exact name."""

    definitions = {
        "golden set": ("golden_sets", "id", "name", "active=1"),
        "model profile": (
            "model_profiles m JOIN endpoints e ON e.id=m.endpoint_id",
            "m.id",
            "m.display_name",
            "m.active=1 AND e.active=1",
        ),
        "judge profile": ("judge_profiles", "id", "name", "active=1"),
    }
    if kind not in definitions:
        raise ValueError("unsupported selector kind")
    table, id_column, name_column, active = definitions[kind]
    connection = factory.connect()
    try:
        try:
            identifier = UUID(selector)
        except ValueError:
            rows = connection.execute(
                f"SELECT {id_column} AS id,{name_column} AS name FROM {table} "  # noqa: S608
                f"WHERE {active} AND {name_column}=? COLLATE NOCASE ORDER BY id LIMIT 6",
                (selector,),
            ).fetchall()
            if len(rows) == 1:
                return UUID(rows[0]["id"])
            if not rows:
                raise ValidationApiError(
                    f"Active {kind} named '{selector}' was not found. Use 'run lookup' to list IDs."
                ) from None
            choices = ", ".join(f"{row['name']} ({row['id']})" for row in rows[:5])
            raise ValidationApiError(f"{kind.title()} name is ambiguous: {choices}") from None
        row = connection.execute(
            f"SELECT {id_column} FROM {table} WHERE {id_column}=? AND {active}",  # noqa: S608
            (str(identifier),),
        ).fetchone()
        if row is None:
            raise ValidationApiError(f"Active {kind} not found. Use 'run lookup' to list IDs.")
        return identifier
    finally:
        connection.close()


def lookup_records(factory: SQLiteConnectionFactory, kind: str) -> list[dict[str, Any]]:
    queries = {
        "golden-sets": """SELECT id,name,version FROM golden_sets
            WHERE active=1 ORDER BY name COLLATE NOCASE,id LIMIT 200""",
        "models": """SELECT m.id,m.display_name AS name,m.model_id,e.name AS endpoint
            FROM model_profiles m JOIN endpoints e ON e.id=m.endpoint_id
            WHERE m.active=1 AND e.active=1
            ORDER BY m.display_name COLLATE NOCASE,m.id LIMIT 200""",
        "judges": """SELECT j.id,j.name,m.display_name AS model,
            j.implementation_id,j.implementation_version
            FROM judge_profiles j JOIN model_profiles m ON m.id=j.model_profile_id
            JOIN endpoints e ON e.id=m.endpoint_id
            WHERE j.active=1 AND m.active=1 AND e.active=1
            ORDER BY j.name COLLATE NOCASE,j.id LIMIT 200""",
    }
    if kind not in queries:
        raise ValueError("unsupported lookup kind")
    connection = factory.connect()
    try:
        return [dict(row) for row in connection.execute(queries[kind]).fetchall()]
    finally:
        connection.close()


def wait_for_terminal(
    run_id: UUID,
    view: RunViewProtocol,
    *,
    timeout: float | None,
    poll_interval: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], str]:
    if poll_interval <= 0:
        raise ValidationApiError("Poll interval must be greater than zero.")
    if timeout is not None and timeout <= 0:
        raise ValidationApiError("Timeout must be greater than zero.")
    started = monotonic()
    while True:
        run = view.run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return run, "terminal"
        if timeout is not None and monotonic() - started >= timeout:
            return run, "timeout"
        sleep(poll_interval)


def build_summary(
    run_id: UUID,
    view: RunViewProtocol,
    aggregates: AggregateProtocol,
    gates: GateProtocol,
    *,
    wait_outcome: str,
    exports: Sequence[str] = (),
) -> dict[str, Any]:
    run = view.run(run_id)
    models = {str(item["id"]): item for item in view.models(run_id)}
    aggregate_rows = aggregates.for_run(run_id)
    model_summaries = []
    for item in aggregate_rows:
        model = models.get(str(item["model_snapshot_id"]), {})
        safe_model = {
            "model_snapshot_id": str(item["model_snapshot_id"]),
            "source_profile_id": model.get("source_profile_id"),
            "display_name": model.get("display_name"),
            "model_id": model.get("model_id"),
        }
        safe_model.update(aggregate_to_dict(item))
        model_summaries.append(safe_model)
    total_costs = [item.get("total_cost") for item in aggregate_rows]
    configured_cost = bool(total_costs) and all(value is not None for value in total_costs)
    total_cost = (
        sum((Decimal(str(value)) for value in total_costs), Decimal(0)) if configured_cost else None
    )
    errors = sum(int(item.get("errors", 0)) for item in aggregate_rows)
    gate_results = gates.results(run_id)
    return {
        "schema_version": "1.0",
        "run_id": str(run_id),
        "name": run["name"],
        "status": run["status"],
        "gate_status": gate_results["status"],
        "wait_outcome": wait_outcome,
        "jobs": {
            "total": int(run.get("total_jobs") or 0),
            "completed": int(run.get("done_jobs") or 0),
            "errors": errors,
        },
        "models": model_summaries,
        "cost": {
            "configured": configured_cost,
            "total": None if total_cost is None else format(total_cost, "f"),
        },
        "exports": list(exports),
    }


def exit_code_for_summary(
    summary: Mapping[str, Any], *, indeterminate_policy: str = "fail"
) -> RunExitCode:
    if summary["wait_outcome"] == "interrupted":
        return RunExitCode.INTERRUPTED
    if summary["wait_outcome"] == "timeout":
        return RunExitCode.TIMEOUT
    status = summary["status"]
    if status == "cancelled":
        return RunExitCode.CANCELLED
    if status in {"failed", "completed_with_errors"} or int(summary["jobs"]["errors"]) > 0:
        return RunExitCode.EXECUTION_ERROR
    gate_status = summary["gate_status"]
    if gate_status == "failed":
        return RunExitCode.GATE_FAILED
    if gate_status == "indeterminate" and indeterminate_policy == "fail":
        return RunExitCode.GATE_INDETERMINATE
    return RunExitCode.SUCCESS


def write_export(path: Path, content: bytes, *, overwrite: bool) -> None:
    """Write one export atomically, refusing existing destinations by default."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    finally:
        temporary.unlink(missing_ok=True)


def perform_exports(
    service: ExportProtocol,
    run_id: UUID,
    destinations: Mapping[str, Path | None],
    *,
    overwrite: bool,
) -> list[str]:
    selected = [(format_, path) for format_, path in destinations.items() if path is not None]
    if not overwrite:
        existing = [str(path) for _, path in selected if path is not None and path.exists()]
        if existing:
            raise FileExistsError(f"Export destination exists: {', '.join(existing)}")
    written = []
    for format_, path in selected:
        if path is None:
            continue
        exporter = getattr(service, f"export_{format_}")
        write_export(path, exporter(run_id), overwrite=overwrite)
        written.append(str(path))
    return written


def render_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def render_human_summary(summary: Mapping[str, Any]) -> str:
    cost = summary["cost"]["total"] if summary["cost"]["configured"] else "not configured"
    lines = [
        f"Run: {summary['run_id']}",
        f"Status: {summary['status']}",
        f"Gates: {summary['gate_status']}",
        f"Jobs: {summary['jobs']['completed']}/{summary['jobs']['total']}",
        f"Errors: {summary['jobs']['errors']}",
        f"Cost: {cost}",
    ]
    for model in summary["models"]:
        name = model.get("display_name") or model.get("model_id") or model["model_snapshot_id"]
        lines.append(
            f"Model {name}: passed={model['passed']} failed={model['failed']} "
            f"errors={model['errors']} score={model.get('weighted_score') or '-'} "
            f"cost={model.get('total_cost') or '-'}"
        )
    return "\n".join(lines)
