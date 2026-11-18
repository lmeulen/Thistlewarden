"""Bounded-memory secret-free run exports and UTC retention cleanup."""

from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TextIO
from uuid import UUID

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_semantic_event
from evaluator.time import Clock, SystemClock, to_utc_iso

_LOG = logging.getLogger(__name__)

ExportFormat = Literal["json", "csv", "html"]
SYNC_EXPORT_RESULT_LIMIT = 50_000
EXPORT_BATCH_SIZE = 100
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


@dataclass(frozen=True, slots=True)
class RunExportArtifact:
    """Restrictive temporary export removed explicitly after consumer closes it."""

    path: Path
    download_name: str
    mimetype: str

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


class RunExportService:
    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        clock: Clock | None = None,
        *,
        result_limit: int = SYNC_EXPORT_RESULT_LIMIT,
        batch_size: int = EXPORT_BATCH_SIZE,
        temporary_directory: Path | None = None,
    ) -> None:
        if result_limit <= 0:
            raise ValueError("result_limit must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._factory, self._clock = factory, clock or SystemClock()
        self._result_limit = result_limit
        self._batch_size = batch_size
        self._temporary_directory = temporary_directory

    def data(self, run_id: UUID) -> dict[str, Any]:
        """Materialize small run data for internal compatibility; exporters never call this."""

        connection = self._factory.connect()
        try:
            run = connection.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if run is None:
                raise NotFoundError("Run not found.")

            def rows(table: str, order: str = "id") -> list[dict[str, Any]]:
                return [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order}",
                        (str(run_id),),
                    )
                ]

            results = [self._export_result(dict(row)) for row in rows("run_results", "job_id")]
            attempts = [
                dict(row)
                for row in connection.execute(
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
            connection.close()

    def create_export(self, run_id: UUID, format_: ExportFormat) -> RunExportArtifact:
        if format_ not in {"json", "csv", "html"}:
            raise ValidationApiError("Export format must be json, csv, or html.")
        connection = self._factory.connect()
        path: Path | None = None
        try:
            connection.execute("BEGIN DEFERRED")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if run is None:
                raise NotFoundError("Run not found.")
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM run_jobs WHERE run_id=?", (str(run_id),)
                ).fetchone()[0]
            )
            if count > self._result_limit:
                raise ValidationApiError(
                    f"Run export has {count} jobs; synchronous exports support at most "
                    f"{self._result_limit}. Split evaluation into smaller runs."
                )
            path = self._temporary_path(format_)
            with path.open("w", encoding="utf-8", newline="") as stream:
                if format_ == "json":
                    self._write_json(connection, run_id, dict(run), stream)
                elif format_ == "csv":
                    self._write_csv(connection, run_id, stream)
                else:
                    self._write_html(connection, run_id, dict(run), stream)
                stream.flush()
                os.fsync(stream.fileno())
            connection.commit()
            artifact = RunExportArtifact(
                path=path,
                download_name=f"{safe_filename(str(run['name']))}.{format_}",
                mimetype={
                    "json": "application/json",
                    "csv": "text/csv",
                    "html": "text/html",
                }[format_],
            )
            log_semantic_event(_LOG, "run_exported", object_id=run_id, format=format_)
            return artifact
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()

    def _temporary_path(self, format_: ExportFormat) -> Path:
        directory = self._temporary_directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="thistlewarden-run-export-",
            suffix=f".{format_}",
            dir=directory,
        )
        os.close(descriptor)
        os.chmod(raw_path, 0o600)
        return Path(raw_path)

    def _iter_query(
        self, connection: sqlite3.Connection, query: str, parameters: tuple[str, ...]
    ) -> Iterator[sqlite3.Row]:
        cursor = connection.execute(query, parameters)
        while batch := cursor.fetchmany(self._batch_size):
            yield from batch

    @staticmethod
    def _json_value(stream: TextIO, value: Any) -> None:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def _json_array(
        self,
        stream: TextIO,
        connection: sqlite3.Connection,
        query: str,
        parameters: tuple[str, ...],
        *,
        transform: Callable[[dict[str, Any]], dict[str, Any]] = dict,
    ) -> None:
        stream.write("[")
        first = True
        for row in self._iter_query(connection, query, parameters):
            if not first:
                stream.write(",")
            self._json_value(stream, transform(dict(row)))
            first = False
        stream.write("]")

    def _write_json(
        self, connection: sqlite3.Connection, run_id: UUID, run: dict[str, Any], stream: TextIO
    ) -> None:
        run_key = (str(run_id),)
        # Keep historical deterministic top-level ordering produced by sort_keys=True.
        stream.write('{"attempts":')
        self._json_array(
            stream,
            connection,
            "SELECT a.* FROM run_attempts a JOIN run_jobs j ON j.id=a.job_id "
            "WHERE j.run_id=? ORDER BY a.job_id,a.attempt_number",
            run_key,
        )
        sections = (
            ("case_snapshots", "run_case_snapshots", "id", dict),
            ("jobs", "run_jobs", "id", dict),
            ("model_snapshots", "run_model_snapshots", "id", dict),
            ("quality_gates", "quality_gate_results", "model_snapshot_id,gate", dict),
            ("results", "run_results", "job_id", self._export_result),
        )
        for key, table, order, transform in sections:
            stream.write(f',"{key}":')
            self._json_array(
                stream,
                connection,
                f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order}",
                run_key,
                transform=transform,
            )
        stream.write(',"run":')
        self._json_value(stream, run)
        stream.write(',"schema_version":"1.0"}')

    def _write_csv(self, connection: sqlite3.Connection, run_id: UUID, stream: TextIO) -> None:
        stream.write("\ufeff")
        writer = csv.DictWriter(
            stream, fieldnames=_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        rows = self._iter_query(
            connection,
            "SELECT * FROM run_results WHERE run_id=? ORDER BY job_id",
            (str(run_id),),
        )
        for row in rows:
            writer.writerow(self._export_result(dict(row)))

    def _write_html(
        self, connection: sqlite3.Connection, run_id: UUID, run: dict[str, Any], stream: TextIO
    ) -> None:
        headings = "".join(f"<th>{column}</th>" for column in _COLUMNS[:-1])
        stream.write(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{html.escape(str(run['name']))}</title>"
            "<style>body{font:14px sans-serif;margin:2rem}table{border-collapse:collapse}"
            "th,td{border:1px solid #999;padding:.3rem}.warning{color:#900}"
            "@media print{.warning{border:1px solid #900;padding:.5rem}}</style></head><body>"
            f"<h1>{html.escape(str(run['name']))}</h1>"
            '<p class="warning">Warning: this report may contain sensitive prompts and answers.'
            f"</p><p>Status: {html.escape(str(run['status']))}</p><table><thead><tr>"
            f"{headings}</tr></thead><tbody>"
        )
        rows = self._iter_query(
            connection,
            "SELECT * FROM run_results WHERE run_id=? ORDER BY job_id",
            (str(run_id),),
        )
        for row in rows:
            item = self._export_result(dict(row))
            stream.write("<tr>")
            for column in _COLUMNS[:-1]:
                stream.write(f"<td>{html.escape(str(item.get(column) or ''))}</td>")
            stream.write("</tr>")
        stream.write("</tbody></table></body></html>")

    @staticmethod
    def _export_result(result: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(result["result_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        category = payload.get("outcome_category") if isinstance(payload, dict) else None
        result["outcome_category"] = (
            "normal"
            if result["status"] != "error"
            else category
            if category in {"provider_guardrail", "provider_error", "processing_error", "unknown"}
            else "unknown"
        )
        result["outcome_stage"] = payload.get("outcome_stage")
        result["outcome_code"] = payload.get("outcome_code")
        evidence = payload.get("provider_evidence")
        result["provider_evidence_json"] = json.dumps(
            evidence if isinstance(evidence, dict) else {}, separators=(",", ":"), sort_keys=True
        )
        return result

    def delete(self, run_id: UUID) -> None:
        self.delete_many([run_id])

    def delete_many(self, run_ids: list[UUID]) -> None:
        unique_ids = list(dict.fromkeys(run_ids))
        if not unique_ids:
            raise ValidationApiError("Select at least one run.")
        connection = self._factory.connect()
        try:
            with transaction(connection):
                placeholders = ",".join("?" for _ in unique_ids)
                rows = connection.execute(
                    f"SELECT id,status FROM runs WHERE id IN ({placeholders})",
                    tuple(str(run_id) for run_id in unique_ids),
                ).fetchall()
                if len(rows) != len(unique_ids):
                    raise NotFoundError("One or more runs were not found.")
                if any(row[1] in {"pending", "running"} for row in rows):
                    raise ConflictError("Active runs cannot be deleted.")
                connection.execute(
                    f"DELETE FROM runs WHERE id IN ({placeholders})",
                    tuple(str(run_id) for run_id in unique_ids),
                )
        finally:
            connection.close()
        for run_id in unique_ids:
            log_semantic_event(_LOG, "run_deleted", object_id=run_id)

    def cleanup(self, retention_days: int) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if retention_days == 0:
            log_semantic_event(_LOG, "retention_cleanup_completed", deleted_runs=0)
            return 0
        cutoff = to_utc_iso(self._clock.now_utc() - timedelta(days=retention_days))
        connection = self._factory.connect()
        try:
            with transaction(connection):
                ids = [
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM runs WHERE status NOT IN ('pending','running') "
                        "AND COALESCE(completed_at,cancelled_at,created_at) < ?",
                        (cutoff,),
                    )
                ]
                connection.executemany("DELETE FROM runs WHERE id=?", [(item,) for item in ids])
        finally:
            connection.close()
        log_semantic_event(_LOG, "retention_cleanup_completed", deleted_runs=len(ids))
        return len(ids)


def serialize_money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, ".8f")
