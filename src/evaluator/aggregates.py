"""Exact per-model run aggregates and deterministic ranking."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from evaluator.database import SQLiteConnectionFactory


def _mean(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal(0)) / Decimal(len(values))


def _percentile(values: list[Decimal], fraction: Decimal) -> Decimal | None:
    """Nearest-rank percentile: sorted value at ceil(p*N), minimum rank one."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(float(fraction * len(ordered))))
    return ordered[rank - 1]


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    size = len(ordered)
    middle = size // 2
    return ordered[middle] if size % 2 else (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def outcome_category(row: sqlite3.Row | dict[str, Any]) -> str:
    if row["status"] != "error":
        return "normal"
    try:
        payload = json.loads(row["result_json"])
    except (json.JSONDecodeError, TypeError):
        return "unknown"
    category = payload.get("outcome_category") if isinstance(payload, dict) else None
    return (
        category
        if category in {"provider_guardrail", "provider_error", "processing_error", "unknown"}
        else "unknown"
    )


def aggregate_rows(rows: Sequence[sqlite3.Row | dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(row["status"] == "passed" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    errors = sum(row["status"] == "error" for row in rows)
    categories = [outcome_category(row) for row in rows]
    guardrails = categories.count("provider_guardrail")
    provider_errors = categories.count("provider_error")
    processing_errors = categories.count("processing_error")
    unknown_errors = categories.count("unknown")
    evaluated = [row for row in rows if row["score"] is not None and row["status"] != "error"]
    weight_sum = sum((Decimal(row["weight"]) for row in evaluated), Decimal(0))
    score = (
        sum((Decimal(row["score"]) * Decimal(row["weight"]) for row in evaluated), Decimal(0))
        / weight_sum
        if weight_sum
        else None
    )

    def values(name: str) -> list[Decimal]:
        return [Decimal(row[name]) for row in rows if row[name] is not None]

    def sum_optional(name: str) -> int | None:
        items = [int(row[name]) for row in rows if row[name] is not None]
        return sum(items) if items else None

    target_costs, judge_costs = values("target_cost"), values("judge_cost")
    known_cost = len(target_costs) == total
    total_cost = sum(target_costs, Decimal(0)) if known_cost else None
    metrics: dict[str, Any] = {
        "total": total,
        "completed": passed + failed,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "guardrails": guardrails,
        "provider_errors": provider_errors,
        "processing_errors": processing_errors,
        "unknown_errors": unknown_errors,
        "guardrail_rate": Decimal(guardrails) / Decimal(total) if total else None,
        "weighted_score": score,
        "pass_rate": Decimal(passed) / Decimal(passed + failed) if passed + failed else None,
        "error_rate": Decimal(errors) / Decimal(total) if total else None,
        "target_cost": sum(target_costs, Decimal(0)) if len(target_costs) == total else None,
        "judge_cost": sum(judge_costs, Decimal(0)) if len(judge_costs) == total else None,
        "total_cost": total_cost,
        "known_total_cost": sum(target_costs, Decimal(0)) if target_costs else None,
        "known_cost_count": len(target_costs),
        "cost_per_evaluated": total_cost / Decimal(passed + failed)
        if total_cost is not None and passed + failed
        else None,
        "cost_per_passed": total_cost / Decimal(passed)
        if total_cost is not None and passed
        else None,
        "input_tokens": sum_optional("input_tokens"),
        "output_tokens": sum_optional("output_tokens"),
        "cached_input_tokens": sum_optional("cached_input_tokens"),
        "reasoning_tokens": sum_optional("reasoning_tokens"),
    }
    for source, prefix in (("target_latency_ms", "latency"), ("ttft_ms", "ttft"), ("tps", "tps")):
        data = values(source)
        metrics[f"{prefix}_mean"] = _mean(data)
        metrics[f"{prefix}_median"] = _median(data)
        metrics[f"{prefix}_p95"] = _percentile(data, Decimal("0.95"))
    return metrics


def rank_aggregates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        score = item["weighted_score"]
        errors = item["error_rate"]
        cost = item["total_cost"]
        return (
            score is None,
            -(score or Decimal(0)),
            errors is None,
            errors or Decimal(0),
            cost is None,
            cost or Decimal(0),
            item["model_snapshot_id"],
        )

    ranked = sorted(items, key=key)
    return [{**item, "rank": index} for index, item in enumerate(ranked, 1)]


class AggregateService:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def for_run(self, run_id: UUID) -> list[dict[str, Any]]:
        connection = self._factory.connect()
        try:
            if (
                connection.execute("SELECT 1 FROM runs WHERE id=?", (str(run_id),)).fetchone()
                is None
            ):
                from evaluator.api.errors import NotFoundError

                raise NotFoundError("Run not found.")
            models = connection.execute(
                """SELECT rms.id,rms.snapshot_json FROM run_model_snapshots rms
                WHERE rms.run_id=? AND EXISTS (
                    SELECT 1 FROM run_jobs j WHERE j.model_snapshot_id=rms.id
                )""",
                (str(run_id),),
            ).fetchall()
            result = []
            for model in models:
                rows = connection.execute(
                    "SELECT * FROM run_results WHERE run_id=? AND model_snapshot_id=?",
                    (str(run_id), model["id"]),
                ).fetchall()
                metrics = aggregate_rows(rows)
                metrics["model_snapshot_id"] = model["id"]
                result.append(metrics)
            return rank_aggregates(result)
        finally:
            connection.close()


def aggregate_to_dict(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: aggregate_to_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [aggregate_to_dict(item) for item in value]
    return value
