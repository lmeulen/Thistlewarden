"""Read-only run GUI view models, filtering, sorting, and display helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory
from evaluator.runs import normalize_case_snapshot

TERMINAL_STATUSES = {"completed", "completed_with_errors", "failed", "cancelled"}
_RESULT_STATUSES = {"passed", "failed", "error"}
_JUDGE_TYPES = {"exact_match", "contains", "regex", "numeric", "json_schema", "llm_judge"}
_JUDGE_TYPE_LABELS = {
    "exact_match": "Exact match",
    "contains": "Contains",
    "regex": "Regex",
    "numeric": "Numeric",
    "json_schema": "JSON Schema",
    "llm_judge": "LLM judge",
}
RESULT_PAGE_SIZE = 100
_SORT_COLUMNS = {
    "case": "cs.case_id",
    "model": "ms.id",
    "status": "rr.status",
    "score": "CAST(rr.score AS REAL)",
    "latency": "CAST(rr.target_latency_ms AS REAL)",
    "ttft": "CAST(rr.ttft_ms AS REAL)",
    "tps": "CAST(rr.tps AS REAL)",
    "cost": "CAST(COALESCE(rr.target_cost,'0') AS REAL)",
}


@dataclass(frozen=True, slots=True)
class ResultQuery:
    status: str | None = None
    model_snapshot_id: UUID | None = None
    case_query: str | None = None
    judge_type: str | None = None
    sort: str = "case"
    direction: str = "asc"
    page: int = 1

    @classmethod
    def parse(cls, values: Any) -> ResultQuery:
        status = (values.get("status") or "").strip() or None
        if status not in _RESULT_STATUSES | {None}:
            raise ValidationApiError("Invalid result filter.")
        model = (values.get("model") or "").strip()
        try:
            model_id = UUID(model) if model else None
        except ValueError as exc:
            raise ValidationApiError("Invalid model filter.") from exc
        judge_type = (values.get("judge_type") or "").strip() or None
        if judge_type not in _JUDGE_TYPES | {None}:
            raise ValidationApiError("Invalid judge type filter.")
        sort = (values.get("sort") or "case").strip()
        direction = (values.get("direction") or "asc").strip()
        if sort not in _SORT_COLUMNS or direction not in {"asc", "desc"}:
            raise ValidationApiError("Invalid sorting.")
        case_query = (values.get("case") or "").strip()[:200] or None
        try:
            page = int(values.get("page") or 1)
        except (TypeError, ValueError) as exc:
            raise ValidationApiError("Invalid page.") from exc
        if page < 1 or page > 100_000:
            raise ValidationApiError("Invalid page.")
        return cls(
            status=status,
            model_snapshot_id=model_id,
            case_query=case_query,
            judge_type=judge_type,
            sort=sort,
            direction=direction,
            page=page,
        )


class RunViewService:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT r.*,COUNT(j.id) total_jobs,
                SUM(CASE WHEN j.status IN ('completed','failed','cancelled')
                    THEN 1 ELSE 0 END) done_jobs
                FROM runs r LEFT JOIN run_jobs j ON j.run_id=r.id
                GROUP BY r.id ORDER BY r.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            runs = [self._run_row(dict(row)) for row in rows]
            self._add_weighted_scores(connection, runs)
            return runs
        finally:
            connection.close()

    def run(self, run_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """SELECT r.*,COUNT(j.id) total_jobs,
                SUM(CASE WHEN j.status IN ('completed','failed','cancelled')
                    THEN 1 ELSE 0 END) done_jobs
                FROM runs r LEFT JOIN run_jobs j ON j.run_id=r.id WHERE r.id=? GROUP BY r.id""",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise NotFoundError("Run not found.")
            run = self._run_row(dict(row))
            self._add_weighted_scores(connection, [run])
            return run
        finally:
            connection.close()

    def rerun_defaults(self, run_id: UUID) -> dict[str, Any]:
        """Return editable creation defaults derived from immutable run sources."""
        connection = self._factory.connect()
        try:
            run = connection.execute(
                """SELECT name,golden_set_id,judge_profile_id,note,
                quality_gates_snapshot_json FROM runs WHERE id=?""",
                (str(run_id),),
            ).fetchone()
            if run is None:
                raise NotFoundError("Run not found.")
            models = connection.execute(
                """SELECT DISTINCT rms.source_profile_id FROM run_model_snapshots rms
                WHERE rms.run_id=? AND EXISTS (
                    SELECT 1 FROM run_jobs j WHERE j.model_snapshot_id=rms.id
                ) ORDER BY rms.source_profile_id""",
                (str(run_id),),
            ).fetchall()
        finally:
            connection.close()
        return {
            "source_name": str(run["name"]),
            "name": f"Rerun of {run['name']}",
            "golden_set_id": str(run["golden_set_id"]),
            "model_profile_ids": [str(row["source_profile_id"]) for row in models],
            "judge_profile_id": str(run["judge_profile_id"] or ""),
            "note": str(run["note"] or ""),
            "quality_gates": json.loads(run["quality_gates_snapshot_json"] or "{}"),
        }

    def golden_set_summaries(self) -> dict[str, dict[str, Any]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT g.id,g.quality_gates_json,COUNT(i.id) active_cases,
                COALESCE(SUM(json_array_length(i.user_prompts_json)),0) active_target_calls
                FROM golden_sets g LEFT JOIN golden_set_items i
                ON i.golden_set_id=g.id AND i.enabled=1 GROUP BY g.id"""
            ).fetchall()
            return {
                row["id"]: {
                    "active_cases": int(row["active_cases"]),
                    "active_target_calls": int(row["active_target_calls"]),
                    "quality_gates": json.loads(row["quality_gates_json"] or "{}"),
                }
                for row in rows
            }
        finally:
            connection.close()

    def models(self, run_id: UUID) -> list[dict[str, Any]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT rms.id,rms.source_profile_id,rms.snapshot_json
                FROM run_model_snapshots rms WHERE rms.run_id=? AND EXISTS (
                    SELECT 1 FROM run_jobs j WHERE j.model_snapshot_id=rms.id
                ) ORDER BY rms.id""",
                (str(run_id),),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "source_profile_id": row["source_profile_id"],
                    **self._model(json.loads(row["snapshot_json"])),
                }
                for row in rows
            ]
        finally:
            connection.close()

    def results(self, run_id: UUID, query: ResultQuery) -> list[dict[str, Any]]:
        conditions: list[str] = ["rr.run_id=?"]
        parameters: list[Any] = [str(run_id)]
        if query.status:
            conditions.append("rr.status=?")
            parameters.append(query.status)
        if query.model_snapshot_id:
            conditions.append("rr.model_snapshot_id=?")
            parameters.append(str(query.model_snapshot_id))
        if query.case_query:
            conditions.append("cs.case_id LIKE ? ESCAPE '\\'")
            escaped = query.case_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.append(f"%{escaped}%")
        if query.judge_type:
            conditions.append("json_extract(cs.snapshot_json,'$.evaluation_method')=?")
            parameters.append(query.judge_type)
        order = _SORT_COLUMNS[query.sort]
        sql = f"""SELECT rr.*,cs.case_id,cs.snapshot_json case_json,ms.snapshot_json model_json
        FROM run_results rr JOIN run_case_snapshots cs ON cs.id=rr.case_snapshot_id
        JOIN run_model_snapshots ms ON ms.id=rr.model_snapshot_id
        WHERE {" AND ".join(conditions)}
        ORDER BY {order} {query.direction.upper()},rr.id LIMIT ? OFFSET ?"""
        parameters.extend([RESULT_PAGE_SIZE + 1, (query.page - 1) * RESULT_PAGE_SIZE])
        connection = self._factory.connect()
        try:
            rows = [self._result(dict(row)) for row in connection.execute(sql, parameters)]
            return rows
        finally:
            connection.close()

    def analysis(self, run_id: UUID) -> dict[str, Any]:
        """Build run-wide and per-judge summaries for server-rendered visualizations."""
        from evaluator.aggregates import aggregate_rows

        connection = self._factory.connect()
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT rr.*,
                    json_extract(cs.snapshot_json,'$.evaluation_method') judge_type
                    FROM run_results rr
                    JOIN run_case_snapshots cs ON cs.id=rr.case_snapshot_id
                    WHERE rr.run_id=?""",
                    (str(run_id),),
                )
            ]
        finally:
            connection.close()
        overall = aggregate_rows(rows)
        grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in _JUDGE_TYPE_LABELS}
        for row in rows:
            judge_type = str(row.get("judge_type") or "")
            if judge_type in grouped:
                grouped[judge_type].append(row)
        judge_types = []
        for name, label in _JUDGE_TYPE_LABELS.items():
            if not grouped[name]:
                continue
            metrics = aggregate_rows(grouped[name])
            passed_percent = _percentage(metrics["passed"], metrics["total"])
            failed_percent = _percentage(metrics["failed"], metrics["total"])
            metrics.update(
                {
                    "name": name,
                    "label": label,
                    "passed_percent": passed_percent,
                    "failed_percent": failed_percent,
                    "error_percent": max(0, 100 - passed_percent - failed_percent),
                }
            )
            judge_types.append(metrics)
        return {"overall": overall, "judge_types": judge_types}

    def result(self, run_id: UUID, result_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """SELECT rr.*,cs.case_id,cs.snapshot_json case_json,ms.snapshot_json model_json
                FROM run_results rr JOIN run_case_snapshots cs ON cs.id=rr.case_snapshot_id
                JOIN run_model_snapshots ms ON ms.id=rr.model_snapshot_id
                WHERE rr.run_id=? AND rr.id=?""",
                (str(run_id), str(result_id)),
            ).fetchone()
            if row is None:
                raise NotFoundError("Result not found.")
            return self._result(dict(row))
        finally:
            connection.close()

    @staticmethod
    def _add_weighted_scores(connection: Any, runs: list[dict[str, Any]]) -> None:
        if not runs:
            return
        run_ids = [run["id"] for run in runs]
        placeholders = ",".join("?" for _ in run_ids)
        rows = connection.execute(
            f"""SELECT run_id,score,weight FROM run_results
            WHERE run_id IN ({placeholders}) AND status!='error' AND score IS NOT NULL""",
            run_ids,
        ).fetchall()
        grouped: dict[str, list[Any]] = {str(run_id): [] for run_id in run_ids}
        for row in rows:
            grouped[row["run_id"]].append(row)
        for run in runs:
            results = grouped[str(run["id"])]
            weight_sum = sum((Decimal(row["weight"]) for row in results), Decimal(0))
            run["weighted_score"] = (
                sum(
                    (Decimal(row["score"]) * Decimal(row["weight"]) for row in results),
                    Decimal(0),
                )
                / weight_sum
                if weight_sum
                else None
            )

    @staticmethod
    def _run_row(row: dict[str, Any]) -> dict[str, Any]:
        total, done = int(row.get("total_jobs") or 0), int(row.get("done_jobs") or 0)
        row["progress_percent"] = 0 if not total else min(100, done * 100 // total)
        row["terminal"] = row["status"] in TERMINAL_STATUSES
        row["recovery_warning"] = bool(row["recovery_warning"])
        return row

    @staticmethod
    def _model(snapshot: dict[str, Any]) -> dict[str, str]:
        return {
            "display_name": str(
                snapshot.get("display_name") or snapshot.get("model_id") or "Model"
            ),
            "model_id": str(snapshot.get("model_id") or ""),
            "endpoint_name": str(snapshot.get("endpoint_name") or ""),
        }

    @classmethod
    def _result(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["evaluation"] = json.loads(row.pop("result_json"))
        if row["status"] == "error":
            category = row["evaluation"].get("outcome_category")
            row["outcome_category"] = (
                category
                if category
                in {"provider_guardrail", "provider_error", "processing_error", "unknown"}
                else "unknown"
            )
        else:
            row["outcome_category"] = "normal"
        row["outcome_stage"] = row["evaluation"].get("outcome_stage")
        row["outcome_code"] = row["evaluation"].get("outcome_code")
        evidence = row["evaluation"].get("provider_evidence")
        row["provider_evidence"] = evidence if isinstance(evidence, dict) else {}
        transcript = row["evaluation"].get("transcript")
        row["transcript"] = transcript if isinstance(transcript, list) else []
        answer = row["evaluation"].get("evaluated_answer")
        if not isinstance(answer, str):
            answer = row["evaluation"].get("candidate")
        row["evaluated_answer"] = answer if isinstance(answer, str) else None
        row["case"] = normalize_case_snapshot(json.loads(row.pop("case_json")))
        config = json.loads(row["case"].get("evaluator_config_json") or "{}")
        row["case"]["expected_value"] = config.get("expected_value") or row["case"].get(
            "expected_answer"
        )
        row["model"] = cls._model(json.loads(row.pop("model_json")))
        row["total_cost"] = decimal_or_none(row.get("target_cost"))
        return row


def _percentage(value: int, total: int) -> int:
    return 0 if not total else round(value * 100 / total)


def chart_percent(value: Any) -> int:
    """Convert a zero-to-one metric to a bounded whole chart percentage."""
    number = decimal_or_none(value)
    if number is None:
        return 0
    return max(0, min(100, round(number * 100)))


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def display_metric(value: Any, *, places: int = 2, suffix: str = "") -> str:
    number = decimal_or_none(value)
    if number is None:
        return "Unknown"
    return f"{number:.{places}f}{suffix}"


def display_money(value: Any) -> str:
    number = decimal_or_none(value)
    return "Unknown" if number is None else f"{number:.8f}"


def status_label(status: str) -> str:
    return {
        "pending": "Pending",
        "running": "Running",
        "completed": "Completed",
        "completed_with_errors": "Completed with errors",
        "failed": "Failed",
        "cancelled": "Cancelled",
        "passed": "Passed",
        "error": "Error",
        "provider_guardrail": "Provider guardrail",
        "provider_error": "Provider error",
        "processing_error": "Processing error",
        "unknown": "Unknown error",
        "not_evaluated": "Not evaluated",
        "indeterminate": "Indeterminate",
    }.get(status, status.replace("_", " ").capitalize())
