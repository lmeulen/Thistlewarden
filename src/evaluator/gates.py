"""Quality gate validation, evaluation, and persistent audit results."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from evaluator.aggregates import AggregateService
from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_context, log_event
from evaluator.time import to_utc_iso, utc_now

_LOG = logging.getLogger(__name__)


class GateOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class QualityGates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_weighted_score: Decimal | None = Field(default=None, ge=0, le=1)
    min_pass_rate: Decimal | None = Field(default=None, ge=0, le=1)
    max_error_rate: Decimal | None = Field(default=None, ge=0, le=1)
    max_mean_latency_ms: Decimal | None = Field(default=None, ge=0)
    max_p95_latency_ms: Decimal | None = Field(default=None, ge=0)
    max_p95_ttft_ms: Decimal | None = Field(default=None, ge=0)
    min_mean_tps: Decimal | None = Field(default=None, ge=0)
    max_total_cost: Decimal | None = Field(default=None, ge=0)

    def configured(self) -> dict[str, Decimal]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


_GATE_LABELS = {
    "min_weighted_score": "Weighted score",
    "min_pass_rate": "Pass rate",
    "max_error_rate": "Error rate",
    "max_mean_latency_ms": "Gemiddelde latency",
    "max_p95_latency_ms": "P95 latency",
    "max_p95_ttft_ms": "P95 TTFT",
    "min_mean_tps": "Gemiddelde TPS",
    "max_total_cost": "Totale kosten",
}

_METRICS = {
    "min_weighted_score": "weighted_score",
    "min_pass_rate": "pass_rate",
    "max_error_rate": "error_rate",
    "max_mean_latency_ms": "latency_mean",
    "max_p95_latency_ms": "latency_p95",
    "max_p95_ttft_ms": "ttft_p95",
    "min_mean_tps": "tps_mean",
}


def evaluate_gate(name: str, measured: Decimal | None, threshold: Decimal) -> GateOutcome:
    if measured is None:
        return GateOutcome.INDETERMINATE
    passed = measured >= threshold if name.startswith("min_") else measured <= threshold
    return GateOutcome.PASSED if passed else GateOutcome.FAILED


class QualityGateService:
    def __init__(self, factory: SQLiteConnectionFactory, aggregates: AggregateService) -> None:
        self._factory, self._aggregates = factory, aggregates

    def set_defaults(self, set_id: UUID, payload: Any) -> QualityGates:
        try:
            gates = QualityGates.model_validate(payload)
        except Exception as exc:
            raise ValidationApiError("Quality gates are invalid.") from exc
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE golden_sets SET quality_gates_json=? WHERE id=?",
                    (gates.model_dump_json(exclude_none=True), str(set_id)),
                )
                if cursor.rowcount != 1:
                    raise NotFoundError("Golden set not found.")
        finally:
            connection.close()
        return gates

    def defaults(self, set_id: UUID) -> QualityGates:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT quality_gates_json FROM golden_sets WHERE id=?", (str(set_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Golden set not found.")
        return QualityGates.model_validate_json(row[0]) if row[0] else QualityGates()

    def evaluate_run(self, run_id: UUID) -> str:
        connection = self._factory.connect()
        try:
            run = connection.execute(
                "SELECT status,quality_gates_snapshot_json FROM runs WHERE id=?", (str(run_id),)
            ).fetchone()
            if run is None:
                return "not_evaluated"
            if run["status"] not in {"completed", "completed_with_errors"}:
                return self._store_status(connection, run_id, "not_evaluated", [])
            raw = run["quality_gates_snapshot_json"]
            gates = QualityGates.model_validate_json(raw) if raw else QualityGates()
            configured = gates.configured()
            if not configured:
                return self._store_status(connection, run_id, "not_evaluated", [])
            aggregates = self._aggregates.for_run(run_id)
            results: list[dict[str, Any]] = []
            for item in aggregates:
                for gate, metric in _METRICS.items():
                    threshold = configured.get(gate)
                    if threshold is None:
                        continue
                    measured = item[metric]
                    results.append(
                        {
                            "model_snapshot_id": item["model_snapshot_id"],
                            "gate": gate,
                            "measured": measured,
                            "threshold": threshold,
                            "outcome": evaluate_gate(gate, measured, threshold),
                        }
                    )
            cost_limit = configured.get("max_total_cost")
            if cost_limit is not None:
                costs = [item["total_cost"] for item in aggregates]
                measured_cost = (
                    None if any(value is None for value in costs) else sum(costs, Decimal(0))
                )
                results.append(
                    {
                        "model_snapshot_id": None,
                        "gate": "max_total_cost",
                        "measured": measured_cost,
                        "threshold": cost_limit,
                        "outcome": evaluate_gate("max_total_cost", measured_cost, cost_limit),
                    }
                )
            outcomes = {item["outcome"] for item in results}
            status = (
                "failed"
                if GateOutcome.FAILED in outcomes
                else ("indeterminate" if GateOutcome.INDETERMINATE in outcomes else "passed")
            )
            stored = self._store_status(connection, run_id, status, results)
            with log_context(run_id=str(run_id)):
                log_event(
                    _LOG,
                    logging.WARNING if status in {"failed", "indeterminate"} else logging.INFO,
                    "quality_gates_evaluated",
                    gate_status=status,
                    count=len(results),
                )
            return stored
        finally:
            connection.close()

    def _store_status(
        self, connection: Any, run_id: UUID, status: str, results: list[dict[str, Any]]
    ) -> str:
        with transaction(connection):
            connection.execute("DELETE FROM quality_gate_results WHERE run_id=?", (str(run_id),))
            now = to_utc_iso(utc_now())
            for item in results:
                connection.execute(
                    "INSERT INTO quality_gate_results VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(run_id),
                        item["model_snapshot_id"],
                        item["gate"],
                        None if item["measured"] is None else str(item["measured"]),
                        str(item["threshold"]),
                        str(item["outcome"]),
                        now,
                    ),
                )
            connection.execute("UPDATE runs SET gate_status=? WHERE id=?", (status, str(run_id)))
        return status

    def results(self, run_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            run = connection.execute(
                "SELECT gate_status FROM runs WHERE id=?", (str(run_id),)
            ).fetchone()
            rows = connection.execute(
                """SELECT q.model_snapshot_id,q.gate,q.measured_value,
                q.threshold_value,q.outcome,rms.snapshot_json model_snapshot_json
                FROM quality_gate_results q
                LEFT JOIN run_model_snapshots rms ON rms.id=q.model_snapshot_id
                WHERE q.run_id=? AND (
                    q.model_snapshot_id IS NULL OR EXISTS (
                        SELECT 1 FROM run_jobs j WHERE j.model_snapshot_id=q.model_snapshot_id
                    )
                ) ORDER BY q.model_snapshot_id,q.gate""",
                (str(run_id),),
            ).fetchall()
            gates = []
            for row in rows:
                gate = dict(row)
                snapshot = gate.pop("model_snapshot_json")
                model = json.loads(snapshot) if snapshot else {}
                gate["model_display_name"] = (
                    model.get("display_name") or model.get("model_id") or None
                )
                gate["label"] = _GATE_LABELS.get(gate["gate"], gate["gate"])
                measured = Decimal(gate["measured_value"]) if gate["measured_value"] else None
                threshold = Decimal(gate["threshold_value"])
                if measured is None:
                    gate["chart_position"] = None
                elif threshold == 0:
                    gate["chart_position"] = 70 if measured == 0 else 100
                else:
                    gate["chart_position"] = max(
                        0, min(100, round(measured / threshold * Decimal(70)))
                    )
                gates.append(gate)
            return {
                "status": "not_evaluated" if run is None else run[0],
                "gates": gates,
            }
        finally:
            connection.close()
