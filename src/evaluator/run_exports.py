"""Complete secret-free run exports and UTC retention cleanup."""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_semantic_event
from evaluator.time import Clock, SystemClock, to_utc_iso

_LOG = logging.getLogger(__name__)

_COLUMNS = (
    "job_id",
    "model_snapshot_id",
    "case_snapshot_id",
    "status",
    "score",
    "weight",
    "target_cost",
    "judge_cost",
    "target_latency_ms",
    "ttft_ms",
    "tps",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
    "outcome_category",
    "outcome_stage",
    "outcome_code",
    "provider_evidence_json",
    "result_json",
)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return cleaned[:100] or "run"


class RunExportService:
    def __init__(self, factory: SQLiteConnectionFactory, clock: Clock | None = None) -> None:
        self._factory, self._clock = factory, clock or SystemClock()

    def data(self, run_id: UUID) -> dict[str, Any]:
        c = self._factory.connect()
        try:
            run = c.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if run is None:
                raise NotFoundError("Run not found.")

            def rows(table: str, order: str = "id") -> list[dict[str, Any]]:
                return [
                    dict(x)
                    for x in c.execute(
                        f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order}", (str(run_id),)
                    )
                ]

            results = rows("run_results", "job_id")
            for result in results:
                try:
                    payload = json.loads(result["result_json"])
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                category = payload.get("outcome_category") if isinstance(payload, dict) else None
                result["outcome_category"] = (
                    "normal"
                    if result["status"] != "error"
                    else category
                    if category
                    in {"provider_guardrail", "provider_error", "processing_error", "unknown"}
                    else "unknown"
                )
                result["outcome_stage"] = payload.get("outcome_stage")
                result["outcome_code"] = payload.get("outcome_code")
                evidence = payload.get("provider_evidence")
                result["provider_evidence_json"] = json.dumps(
                    evidence if isinstance(evidence, dict) else {},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            attempts = [
                dict(x)
                for x in c.execute(
                    "SELECT a.* FROM run_attempts a JOIN run_jobs j ON j.id=a.job_id "
                    "WHERE j.run_id=? ORDER BY a.job_id,a.attempt_number",
                    (str(run_id),),
                )
            ]
            return {
                "schema_version": "1.0",
                "run": dict(run),
                "case_snapshots": rows("run_case_snapshots"),
                "model_snapshots": rows("run_model_snapshots"),
                "jobs": rows("run_jobs"),
                "attempts": attempts,
                "results": results,
                "quality_gates": rows("quality_gate_results", "model_snapshot_id,gate"),
            }
        finally:
            c.close()

    def export_json(self, run_id: UUID) -> bytes:
        result = json.dumps(
            self.data(run_id), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        log_semantic_event(_LOG, "run_exported", object_id=run_id, format="json")
        return result

    def export_csv(self, run_id: UUID) -> bytes:
        data = self.data(run_id)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(data["results"])
        result = output.getvalue().encode("utf-8-sig")
        log_semantic_event(_LOG, "run_exported", object_id=run_id, format="csv")
        return result

    def export_html(self, run_id: UUID) -> bytes:
        data = self.data(run_id)
        run = data["run"]
        rows = []
        for item in data["results"]:
            rows.append(
                "<tr>"
                + "".join(f"<td>{html.escape(str(item.get(k) or ''))}</td>" for k in _COLUMNS[:-1])
                + "</tr>"
            )
        payload = html.escape(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        headings = "".join(f"<th>{x}</th>" for x in _COLUMNS[:-1])
        document = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{html.escape(run['name'])}</title>"
            "<style>body{font:14px sans-serif;margin:2rem}table{border-collapse:collapse}"
            "th,td{border:1px solid #999;padding:.3rem}.warning{color:#900}"
            "@media print{.warning{border:1px solid #900;padding:.5rem}}</style></head><body>"
            f"<h1>{html.escape(run['name'])}</h1>"
            '<p class="warning">Warning: this report may contain sensitive prompts and answers.'
            f"</p><p>Status: {html.escape(run['status'])}</p><table><thead><tr>"
            f"{headings}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"<details><summary>Complete data</summary><pre>{payload}</pre></details>"
            "</body></html>"
        )
        result = document.encode()
        log_semantic_event(_LOG, "run_exported", object_id=run_id, format="html")
        return result

    def delete(self, run_id: UUID) -> None:
        self.delete_many([run_id])

    def delete_many(self, run_ids: list[UUID]) -> None:
        unique_ids = list(dict.fromkeys(run_ids))
        if not unique_ids:
            raise ValidationApiError("Select at least one run.")
        c = self._factory.connect()
        try:
            with transaction(c):
                placeholders = ",".join("?" for _ in unique_ids)
                rows = c.execute(
                    f"SELECT id,status FROM runs WHERE id IN ({placeholders})",
                    tuple(str(run_id) for run_id in unique_ids),
                ).fetchall()
                if len(rows) != len(unique_ids):
                    raise NotFoundError("One or more runs were not found.")
                if any(row[1] in {"pending", "running"} for row in rows):
                    raise ConflictError("Active runs cannot be deleted.")
                c.execute(
                    f"DELETE FROM runs WHERE id IN ({placeholders})",
                    tuple(str(run_id) for run_id in unique_ids),
                )
        finally:
            c.close()
        for run_id in unique_ids:
            log_semantic_event(_LOG, "run_deleted", object_id=run_id)

    def cleanup(self, retention_days: int) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if retention_days == 0:
            log_semantic_event(_LOG, "retention_cleanup_completed", deleted_runs=0)
            return 0
        cutoff = to_utc_iso(self._clock.now_utc() - timedelta(days=retention_days))
        c = self._factory.connect()
        try:
            with transaction(c):
                ids = [
                    r[0]
                    for r in c.execute(
                        "SELECT id FROM runs WHERE status NOT IN ('pending','running') "
                        "AND COALESCE(completed_at,cancelled_at,created_at) < ?",
                        (cutoff,),
                    )
                ]
                c.executemany("DELETE FROM runs WHERE id=?", [(x,) for x in ids])
        finally:
            c.close()
        log_semantic_event(_LOG, "retention_cleanup_completed", deleted_runs=len(ids))
        return len(ids)


def serialize_money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, ".8f")
