"""Read-only, snapshot-based comparison of completed historical runs."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory
from evaluator.run_views import TERMINAL_STATUSES

ComparisonFilter = Literal["all", "regression", "improvement", "unchanged", "incomparable"]
_FILTERS = {"all", "regression", "improvement", "unchanged", "incomparable"}
_METRICS = (
    "score",
    "target_latency_ms",
    "ttft_ms",
    "tps",
    "input_tokens",
    "output_tokens",
    "target_cost",
    "judge_cost",
)


class RunComparisonService:
    """Compare immutable run snapshots and results without mutating either run."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def candidates(self, run_id: UUID) -> list[dict[str, Any]]:
        current = self._run(run_id)
        self._require_completed(current)
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT id,name,status,created_at,completed_at FROM runs
                WHERE id!=? AND status IN ('completed','completed_with_errors')
                ORDER BY completed_at DESC,created_at DESC,id""",
                (str(run_id),),
            ).fetchall()
        finally:
            connection.close()
        candidates = []
        for row in rows:
            summary = self._compare(run_id, UUID(row["id"]), include_records=False)
            candidates.append(
                {
                    **dict(row),
                    "compatibility": summary["compatibility"],
                    "comparable_records": summary["summary"]["comparable"],
                    "warnings": summary["warnings"],
                }
            )
        return candidates

    def compare(
        self,
        run_id: UUID,
        baseline_id: UUID,
        *,
        filter_: ComparisonFilter = "all",
    ) -> dict[str, Any]:
        if filter_ not in _FILTERS:
            raise ValidationApiError("Invalid comparison filter.")
        comparison = self._compare(run_id, baseline_id, include_records=True)
        if filter_ != "all":
            comparison["records"] = [
                row for row in comparison["records"] if row["classification"] == filter_
            ]
        comparison["filter"] = filter_
        return comparison

    def export_json(self, run_id: UUID, baseline_id: UUID) -> str:
        """Return deterministic report; no generation timestamps or mutable configuration."""
        return (
            json.dumps(
                self.compare(run_id, baseline_id),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
            + "\n"
        )

    def _compare(self, run_id: UUID, baseline_id: UUID, *, include_records: bool) -> dict[str, Any]:
        if run_id == baseline_id:
            raise ValidationApiError("A run cannot be its own baseline.")
        candidate, baseline = self._run(run_id), self._run(baseline_id)
        self._require_completed(candidate)
        self._require_completed(baseline)
        candidate_rows = self._rows(run_id)
        baseline_rows = self._rows(baseline_id)
        candidate_map = {row["key"]: row for row in candidate_rows}
        baseline_map = {row["key"]: row for row in baseline_rows}
        records = []
        for key in sorted(candidate_map.keys() | baseline_map.keys()):
            records.append(
                self._record(
                    candidate_map.get(key),
                    baseline_map.get(key),
                    candidate.get("judge_snapshot_json"),
                    baseline.get("judge_snapshot_json"),
                )
            )
        counts = Counter(row["classification"] for row in records)
        comparable = sum(counts[name] for name in ("regression", "improvement", "unchanged"))
        changed = sum(row["change_kind"] == "changed" for row in records)
        added = sum(row["change_kind"] == "added" for row in records)
        removed = sum(row["change_kind"] == "removed" for row in records)
        if records and comparable == len(records):
            compatibility = "compatible"
        elif comparable:
            compatibility = "partially_compatible"
        else:
            compatibility = "incompatible"
        warnings = self._warnings(records, candidate, baseline)
        result: dict[str, Any] = {
            "candidate": _run_public(candidate),
            "baseline": _run_public(baseline),
            "compatibility": compatibility,
            "warnings": warnings,
            "summary": {
                "total_records": len(records),
                "comparable": comparable,
                "regressions": counts["regression"],
                "improvements": counts["improvement"],
                "unchanged": counts["unchanged"],
                "incomparable": counts["incomparable"],
                "added": added,
                "removed": removed,
                "changed": changed,
            },
            "aggregate_deltas": self._aggregate_deltas(records),
            "methodology": {
                "matching": "case source item and model source profile identifiers",
                "comparable": (
                    "Both results exist; case, evaluator, weight, model, inference, and applicable "
                    "judge snapshots are identical; neither result is an error."
                ),
                "errors": "Errors are incomparable and excluded from score/performance deltas.",
                "missing_metrics": (
                    "A metric delta is null unless both records contain that metric."
                ),
                "weights": "Changed case weights make records incomparable.",
                "error_rate": (
                    "Calculated over all matched records with identical input snapshots, including "
                    "error results; added, removed, and changed records are excluded."
                ),
            },
        }
        if include_records:
            result["records"] = records
        return result

    def _rows(self, run_id: UUID) -> list[dict[str, Any]]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT j.run_id,rr.id result_id,rr.status,rr.score,
                rr.target_latency_ms,rr.ttft_ms,
                rr.tps,rr.input_tokens,rr.output_tokens,rr.target_cost,rr.judge_cost,
                cs.source_item_id,cs.case_id,cs.weight,cs.snapshot_json case_json,
                ms.source_profile_id,ms.snapshot_json model_json
                FROM run_jobs j
                JOIN run_case_snapshots cs ON cs.id=j.case_snapshot_id
                JOIN run_model_snapshots ms ON ms.id=j.model_snapshot_id
                LEFT JOIN run_results rr ON rr.job_id=j.id
                WHERE j.run_id=? ORDER BY cs.case_id,ms.source_profile_id,j.id""",
                (str(run_id),),
            ).fetchall()
        finally:
            connection.close()
        result = []
        for value in rows:
            row = dict(value)
            row["case_snapshot"] = json.loads(row.pop("case_json"))
            row["model_snapshot"] = json.loads(row.pop("model_json"))
            row["key"] = (row["source_item_id"], row["source_profile_id"])
            result.append(row)
        return result

    @staticmethod
    def _record(
        candidate: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        candidate_judge_raw: str | None,
        baseline_judge_raw: str | None,
    ) -> dict[str, Any]:
        source = candidate or baseline
        if source is None:  # pragma: no cover - caller iterates union of existing keys
            raise RuntimeError("Comparison record has no source.")
        base: dict[str, Any] = {
            "case_id": source["case_id"],
            "source_item_id": source["source_item_id"],
            "source_profile_id": source["source_profile_id"],
            "model": _model_public(source["model_snapshot"]),
            "candidate_result_id": candidate["result_id"] if candidate else None,
            "baseline_result_id": baseline["result_id"] if baseline else None,
            "candidate_result_path": None,
            "baseline_result_path": None,
            "candidate_status": candidate["status"] if candidate else None,
            "baseline_status": baseline["status"] if baseline else None,
            "metric_deltas": dict.fromkeys(_METRICS),
        }
        if candidate is not None and candidate["result_id"] is not None:
            base["candidate_result_path"] = (
                f"/runs/{candidate['run_id']}/results/{candidate['result_id']}"
            )
        if baseline is not None and baseline["result_id"] is not None:
            base["baseline_result_path"] = (
                f"/runs/{baseline['run_id']}/results/{baseline['result_id']}"
            )
        if baseline is None:
            return {
                **base,
                "classification": "incomparable",
                "change_kind": "added",
                "reasons": ["Record was added in candidate run."],
            }
        if candidate is None:
            return {
                **base,
                "classification": "incomparable",
                "change_kind": "removed",
                "reasons": ["Record is absent from candidate run."],
            }
        reasons = []
        if _canonical(candidate["case_snapshot"]) != _canonical(baseline["case_snapshot"]):
            reasons.append("Case, evaluator configuration, or weight snapshot changed.")
        if _canonical(candidate["model_snapshot"]) != _canonical(baseline["model_snapshot"]):
            reasons.append("Model identity, endpoint, pricing, or inference snapshot changed.")
        uses_judge = candidate["case_snapshot"].get("evaluation_method") == "llm_judge"
        if uses_judge and _canonical_json(candidate_judge_raw) != _canonical_json(
            baseline_judge_raw
        ):
            reasons.append("Judge preset, implementation, model, or inference snapshot changed.")
        if reasons:
            return {
                **base,
                "classification": "incomparable",
                "change_kind": "changed",
                "reasons": reasons,
            }
        if candidate["result_id"] is None or baseline["result_id"] is None:
            return {
                **base,
                "classification": "incomparable",
                "change_kind": "missing_result",
                "reasons": [
                    "At least one snapshotted job has no result; metric deltas are excluded."
                ],
            }
        if candidate["status"] == "error" or baseline["status"] == "error":
            return {
                **base,
                "classification": "incomparable",
                "change_kind": "result_error",
                "reasons": [
                    "At least one result is an error; quality and performance deltas are excluded."
                ],
            }
        for metric in _METRICS:
            base["metric_deltas"][metric] = _delta(candidate.get(metric), baseline.get(metric))
        transition = (baseline["status"], candidate["status"])
        classification = (
            "regression"
            if transition == ("passed", "failed")
            else "improvement"
            if transition == ("failed", "passed")
            else "unchanged"
        )
        return {**base, "classification": classification, "change_kind": "none", "reasons": []}

    @staticmethod
    def _aggregate_deltas(records: list[dict[str, Any]]) -> dict[str, Any]:
        comparable = [row for row in records if row["classification"] != "incomparable"]
        metric_deltas: dict[str, Any] = {}
        for metric in _METRICS:
            values = [row["metric_deltas"][metric] for row in comparable]
            known = [Decimal(value) for value in values if value is not None]
            metric_deltas[metric] = str(sum(known, Decimal(0)) / len(known)) if known else None
        exact_inputs = [row for row in records if row["change_kind"] in {"none", "result_error"}]
        if exact_inputs:
            baseline_errors = sum(row["baseline_status"] == "error" for row in exact_inputs)
            candidate_errors = sum(row["candidate_status"] == "error" for row in exact_inputs)
            error_rate_delta = Decimal(candidate_errors - baseline_errors) / Decimal(
                len(exact_inputs)
            )
        else:
            error_rate_delta = None
        metric_deltas["error_rate"] = (
            str(error_rate_delta) if error_rate_delta is not None else None
        )
        metric_deltas["record_count"] = len(comparable)
        return metric_deltas

    @staticmethod
    def _warnings(
        records: list[dict[str, Any]], candidate: dict[str, Any], baseline: dict[str, Any]
    ) -> list[str]:
        warnings = []
        if candidate["golden_set_id"] != baseline["golden_set_id"]:
            warnings.append("Runs reference different golden-set identities.")
        kinds = Counter(row["change_kind"] for row in records)
        if kinds["added"] or kinds["removed"]:
            warnings.append(
                f"Record membership changed: {kinds['added']} added, {kinds['removed']} removed."
            )
        if kinds["changed"]:
            warnings.append(f"{kinds['changed']} record(s) have changed immutable snapshots.")
        if kinds["result_error"]:
            warnings.append(
                f"{kinds['result_error']} matched record(s) contain errors and are incomparable."
            )
        if kinds["missing_result"]:
            warnings.append(
                f"{kinds['missing_result']} matched job(s) have a missing result "
                "and are incomparable."
            )
        return warnings

    def _run(self, run_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Run not found.")
        return dict(row)

    @staticmethod
    def _require_completed(run: dict[str, Any]) -> None:
        if run["status"] not in {"completed", "completed_with_errors"}:
            if run["status"] in TERMINAL_STATUSES:
                raise ConflictError("Only completed runs can be compared.")
            raise ConflictError("Wait until run completes before comparing it.")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_json(value: str | None) -> str:
    return _canonical(json.loads(value)) if value else "null"


def _delta(candidate: Any, baseline: Any) -> str | None:
    if candidate is None or baseline is None:
        return None
    return str(Decimal(str(candidate)) - Decimal(str(baseline)))


def _model_public(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        "display_name": str(snapshot.get("display_name") or snapshot.get("model_id") or "Model"),
        "model_id": str(snapshot.get("model_id") or ""),
    }


def _run_public(run: dict[str, Any]) -> dict[str, Any]:
    return {key: run[key] for key in ("id", "name", "status", "created_at", "completed_at")}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")
