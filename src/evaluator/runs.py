"""Immutable run snapshots and durable fenced SQLite job queue."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.gates import QualityGates
from evaluator.time import Clock, SystemClock, to_utc_iso


class LeaseLost(RuntimeError):
    """Worker fencing token no longer owns a live lease."""


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    golden_set_id: UUID
    model_profile_ids: list[UUID] = Field(min_length=1, max_length=20)
    judge_profile_id: UUID | None = None
    note: str | None = Field(default=None, max_length=100_000)
    quality_gates: QualityGates | None = None


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    run_id: UUID
    case_snapshot_id: UUID
    model_snapshot_id: UUID
    owner: str
    generation: int
    expires_at: datetime
    recovered_after_interruption: bool


class RunQueue:
    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        clock: Clock | None = None,
        lease_seconds: int = 60,
        max_profiles: int = 20,
    ) -> None:
        self._factory, self._clock = factory, clock or SystemClock()
        self._lease_seconds, self._max_profiles = lease_seconds, max_profiles

    def create_run(self, payload: Any) -> UUID:
        try:
            data = RunInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        if len(set(data.model_profile_ids)) != len(data.model_profile_ids):
            raise ValidationApiError("Model profiles must be unique.")
        if len(data.model_profile_ids) > self._max_profiles:
            raise ValidationApiError("Too many model profiles selected.")
        run_id, now = uuid4(), self._clock.now_utc()
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                set_row = connection.execute(
                    "SELECT * FROM golden_sets WHERE id=? AND active=1", (str(data.golden_set_id),)
                ).fetchone()
                if set_row is None:
                    raise ValidationApiError("Active golden set not found.")
                cases = connection.execute(
                    "SELECT * FROM golden_set_items WHERE golden_set_id=? "
                    "AND enabled=1 ORDER BY sort_order",
                    (str(data.golden_set_id),),
                ).fetchall()
                if not cases:
                    raise ValidationApiError("Run requires at least one active case.")
                models = []
                for profile_id in data.model_profile_ids:
                    row = connection.execute(
                        """SELECT m.*,e.completions_url,e.name AS endpoint_name,
                        e.api_key_header FROM model_profiles m JOIN endpoints e
                        ON e.id=m.endpoint_id
                        WHERE m.id=? AND m.active=1 AND e.active=1""",
                        (str(profile_id),),
                    ).fetchone()
                    if row is None:
                        raise ValidationApiError("Active model profile not found.")
                    models.append(row)
                judge_snapshot = self._judge_snapshot(
                    connection, data.judge_profile_id or set_row["judge_profile_id"]
                )
                if judge_snapshot[1] is not None and all(
                    str(row["id"]) != judge_snapshot[1]["model_profile_id"] for row in models
                ):
                    judge_model = connection.execute(
                        """SELECT m.*,e.completions_url,e.name AS endpoint_name,
                        e.api_key_header FROM model_profiles m JOIN endpoints e
                        ON e.id=m.endpoint_id WHERE m.id=? AND m.active=1 AND e.active=1""",
                        (judge_snapshot[1]["model_profile_id"],),
                    ).fetchone()
                    if judge_model is None:
                        raise ValidationApiError("Active judge model profile not found.")
                    models.append(judge_model)
                gates = (
                    data.quality_gates.model_dump(mode="json", exclude_none=True)
                    if "quality_gates" in data.model_fields_set and data.quality_gates is not None
                    else {}
                    if "quality_gates" in data.model_fields_set
                    else json.loads(set_row["quality_gates_json"])
                    if set_row["quality_gates_json"]
                    else {}
                )
                connection.execute(
                    """INSERT INTO runs(
                    id,name,golden_set_id,status,judge_profile_id,note,cancelled_at,created_at,
                    started_at,completed_at,recovery_warning,judge_snapshot_json,
                    quality_gates_snapshot_json,gate_status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(run_id),
                        data.name.strip(),
                        str(data.golden_set_id),
                        "pending",
                        judge_snapshot[0],
                        data.note,
                        None,
                        to_utc_iso(now),
                        None,
                        None,
                        0,
                        json.dumps(judge_snapshot[1], separators=(",", ":"))
                        if judge_snapshot[1]
                        else None,
                        json.dumps(gates, separators=(",", ":")),
                        "not_evaluated",
                    ),
                )
                case_ids = []
                for row in cases:
                    snapshot_id = uuid4()
                    case_ids.append(snapshot_id)
                    snapshot = {
                        key: row[key]
                        for key in (
                            "case_id",
                            "prompt",
                            "system_prompt",
                            "expected_answer",
                            "evaluation_method",
                            "evaluator_config_json",
                            "category",
                            "tags_json",
                            "weight",
                        )
                    }
                    connection.execute(
                        "INSERT INTO run_case_snapshots VALUES (?,?,?,?,?,?)",
                        (
                            str(snapshot_id),
                            str(run_id),
                            row["id"],
                            row["case_id"],
                            row["weight"],
                            json.dumps(snapshot, separators=(",", ":")),
                        ),
                    )
                model_ids = []
                target_profile_ids = {str(value) for value in data.model_profile_ids}
                for row in models:
                    snapshot_id = uuid4()
                    if row["id"] in target_profile_ids:
                        model_ids.append(snapshot_id)
                    excluded = {"api_key_secret_id"}
                    snapshot = {key: row[key] for key in row.keys() if key not in excluded}
                    connection.execute(
                        "INSERT INTO run_model_snapshots VALUES (?,?,?,?,?)",
                        (
                            str(snapshot_id),
                            str(run_id),
                            row["id"],
                            row["endpoint_id"],
                            json.dumps(snapshot, separators=(",", ":")),
                        ),
                    )
                for case_id in case_ids:
                    for model_id in model_ids:
                        job_id = uuid4()
                        connection.execute(
                            "INSERT INTO run_jobs(id,run_id,case_snapshot_id,model_snapshot_id,"
                            "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                            (
                                str(job_id),
                                str(run_id),
                                str(case_id),
                                str(model_id),
                                "pending",
                                to_utc_iso(now),
                                to_utc_iso(now),
                            ),
                        )
        finally:
            connection.close()
        return run_id

    def recover_expired(self) -> dict[str, int]:
        """Expose expired interrupted work; generation changes only on next claim."""
        now = to_utc_iso(self._clock.now_utc())
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                rows = connection.execute(
                    "SELECT id,run_id FROM run_jobs WHERE status='running' "
                    "AND lease_expires_at <= ?",
                    (now,),
                ).fetchall()
                run_ids = {row["run_id"] for row in rows}
                for run_id in run_ids:
                    connection.execute(
                        "UPDATE runs SET recovery_warning=1,status='running' WHERE id=?",
                        (run_id,),
                    )
                return {"runs": len(run_ids), "jobs": len(rows)}
        finally:
            connection.close()

    def claim(self, owner: str) -> JobLease | None:
        now, expiry = (
            self._clock.now_utc(),
            self._clock.now_utc() + timedelta(seconds=self._lease_seconds),
        )
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                row = connection.execute(
                    """SELECT j.* FROM run_jobs j JOIN runs r ON r.id=j.run_id
                    WHERE r.status IN ('pending','running') AND (
                    j.status='pending' OR (j.status='running' AND j.lease_expires_at <= ?))
                    ORDER BY j.created_at,j.id LIMIT 1""",
                    (to_utc_iso(now),),
                ).fetchone()
                if row is None:
                    return None
                recovered = row["status"] == "running"
                generation = int(row["lease_generation"]) + 1
                cursor = connection.execute(
                    """UPDATE run_jobs SET status='running',lease_owner=?,
                    lease_generation=?,lease_expires_at=?,recovered_after_interruption=?,updated_at=?
                    WHERE id=? AND lease_generation=?""",
                    (
                        owner,
                        generation,
                        to_utc_iso(expiry),
                        int(recovered),
                        to_utc_iso(now),
                        row["id"],
                        row["lease_generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                connection.execute(
                    "UPDATE runs SET status='running',started_at=COALESCE(started_at,?), "
                    "recovery_warning=MAX(recovery_warning,?) WHERE id=?",
                    (to_utc_iso(now), int(recovered), row["run_id"]),
                )
                return JobLease(
                    UUID(row["id"]),
                    UUID(row["run_id"]),
                    UUID(row["case_snapshot_id"]),
                    UUID(row["model_snapshot_id"]),
                    owner,
                    generation,
                    expiry,
                    recovered,
                )
        finally:
            connection.close()

    def heartbeat(self, lease: JobLease) -> JobLease:
        now = self._clock.now_utc()
        expiry = now + timedelta(seconds=self._lease_seconds)
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    """UPDATE run_jobs SET lease_expires_at=?,updated_at=?
                    WHERE id=? AND status='running' AND lease_owner=? AND lease_generation=?
                    AND lease_expires_at > ? AND EXISTS (
                        SELECT 1 FROM runs WHERE runs.id=run_jobs.run_id
                        AND runs.status IN ('pending','running'))""",
                    (
                        to_utc_iso(expiry),
                        to_utc_iso(now),
                        str(lease.job_id),
                        lease.owner,
                        lease.generation,
                        to_utc_iso(now),
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost
        finally:
            connection.close()
        return JobLease(
            lease.job_id,
            lease.run_id,
            lease.case_snapshot_id,
            lease.model_snapshot_id,
            lease.owner,
            lease.generation,
            expiry,
            lease.recovered_after_interruption,
        )

    def record_attempt(
        self, lease: JobLease, category: str | None, metadata: dict[str, Any]
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                count = (
                    int(
                        connection.execute(
                            "SELECT attempt_count FROM run_jobs WHERE id=?", (str(lease.job_id),)
                        ).fetchone()[0]
                    )
                    + 1
                )
                connection.execute(
                    "INSERT INTO run_attempts VALUES (?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(lease.job_id),
                        count,
                        category,
                        json.dumps(metadata, separators=(",", ":")),
                        to_utc_iso(self._clock.now_utc()),
                    ),
                )
                connection.execute(
                    "UPDATE run_jobs SET attempt_count=? WHERE id=?", (count, str(lease.job_id))
                )
        finally:
            connection.close()

    def complete(
        self,
        lease: JobLease,
        *,
        status: Literal["passed", "failed", "error"],
        score: str | None,
        weight: str,
        result: dict[str, Any],
        metrics: dict[str, Any] | None = None,
    ) -> None:
        now, metrics = self._clock.now_utc(), metrics or {}
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                connection.execute(
                    "INSERT INTO run_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(lease.job_id),
                        str(lease.run_id),
                        str(lease.model_snapshot_id),
                        str(lease.case_snapshot_id),
                        status,
                        score,
                        weight,
                        json.dumps(result, separators=(",", ":")),
                        metrics.get("target_cost"),
                        metrics.get("judge_cost"),
                        metrics.get("target_latency_ms"),
                        metrics.get("ttft_ms"),
                        metrics.get("tps"),
                        metrics.get("input_tokens"),
                        metrics.get("output_tokens"),
                        metrics.get("cached_input_tokens"),
                        metrics.get("reasoning_tokens"),
                        to_utc_iso(now),
                    ),
                )
                cursor = connection.execute(
                    "UPDATE run_jobs SET status=?,lease_owner=NULL,lease_expires_at=NULL,"
                    "updated_at=? WHERE id=? AND lease_owner=? AND lease_generation=?",
                    (
                        "failed" if status == "error" else "completed",
                        to_utc_iso(now),
                        str(lease.job_id),
                        lease.owner,
                        lease.generation,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost
        finally:
            connection.close()

    def fail(self, lease: JobLease, error_code: str, metadata: dict[str, Any]) -> None:
        now = self._clock.now_utc()
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                connection.execute(
                    "INSERT INTO run_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(lease.job_id),
                        str(lease.run_id),
                        str(lease.model_snapshot_id),
                        str(lease.case_snapshot_id),
                        "error",
                        None,
                        "1",
                        json.dumps(metadata, separators=(",", ":")),
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        to_utc_iso(now),
                    ),
                )
                cursor = connection.execute(
                    "UPDATE run_jobs SET status='failed',error_code=?,lease_owner=NULL,"
                    "lease_expires_at=NULL,updated_at=? WHERE id=? AND lease_owner=? "
                    "AND lease_generation=?",
                    (error_code, to_utc_iso(now), str(lease.job_id), lease.owner, lease.generation),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost
        finally:
            connection.close()

    def cancel(self, run_id: UUID) -> None:
        now = to_utc_iso(self._clock.now_utc())
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE runs SET status='cancelled',cancelled_at=? WHERE id=? "
                    "AND status IN ('pending','running')",
                    (now, str(run_id)),
                )
                if cursor.rowcount != 1:
                    raise ValidationApiError("Run cannot be cancelled.")
                connection.execute(
                    "UPDATE run_jobs SET status='cancelled',updated_at=? WHERE run_id=? "
                    "AND status='pending'",
                    (now, str(run_id)),
                )
        finally:
            connection.close()

    def refresh_run_status(self, run_id: UUID) -> str:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                run = connection.execute(
                    "SELECT status FROM runs WHERE id=?", (str(run_id),)
                ).fetchone()
                if run is None:
                    raise NotFoundError("Run not found.")
                statuses = [
                    row[0]
                    for row in connection.execute(
                        "SELECT status FROM run_jobs WHERE run_id=?", (str(run_id),)
                    ).fetchall()
                ]
                if run[0] == "cancelled":
                    status = "cancelled"
                elif any(value in {"pending", "running"} for value in statuses):
                    status = "running"
                elif statuses and all(value == "failed" for value in statuses):
                    status = "failed"
                elif any(value == "failed" for value in statuses):
                    status = "completed_with_errors"
                else:
                    status = "completed"
                completed = (
                    to_utc_iso(self._clock.now_utc())
                    if status not in {"running", "pending"}
                    else None
                )
                connection.execute(
                    "UPDATE runs SET status=?,completed_at=COALESCE(completed_at,?) WHERE id=?",
                    (status, completed, str(run_id)),
                )
                return status
        finally:
            connection.close()

    def rerun_failures(self, run_id: UUID, name: str) -> UUID:
        connection = self._factory.connect()
        try:
            run = connection.execute(
                "SELECT golden_set_id,judge_profile_id FROM runs WHERE id=?", (str(run_id),)
            ).fetchone()
            rows = connection.execute(
                """SELECT DISTINCT rms.source_profile_id FROM run_jobs j
                JOIN run_model_snapshots rms ON rms.id=j.model_snapshot_id
                WHERE j.run_id=? AND j.status='failed'""",
                (str(run_id),),
            ).fetchall()
        finally:
            connection.close()
        if run is None or not rows:
            raise ValidationApiError("No failed jobs to retry.")
        return self.create_run(
            {
                "name": name,
                "golden_set_id": run[0],
                "model_profile_ids": [row[0] for row in rows],
                "judge_profile_id": run[1],
            }
        )

    def model_snapshot_for_source(self, run_id: UUID, source_profile_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """SELECT snapshot_json FROM run_model_snapshots
                WHERE run_id=? AND source_profile_id=?""",
                (str(run_id), str(source_profile_id)),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Judge model snapshot not found.")
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise ValueError("model snapshot must be a JSON object")
        return value

    def judge_snapshot(self, run_id: UUID) -> dict[str, Any] | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT judge_snapshot_json FROM runs WHERE id=?", (str(run_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Run not found.")
        value = None if row[0] is None else json.loads(row[0])
        if value is not None and not isinstance(value, dict):
            raise ValueError("judge snapshot must be a JSON object")
        return value

    def snapshot(self, snapshot_id: UUID, kind: Literal["case", "model"]) -> dict[str, Any]:
        table = "run_case_snapshots" if kind == "case" else "run_model_snapshots"
        connection = self._factory.connect()
        try:
            row = connection.execute(
                f"SELECT snapshot_json FROM {table} WHERE id=?", (str(snapshot_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise NotFoundError("Snapshot not found.")
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise ValueError("snapshot must be a JSON object")
        return value

    def _assert_lease(self, connection: sqlite3.Connection, lease: JobLease) -> None:
        row = connection.execute(
            """SELECT 1 FROM run_jobs j JOIN runs r ON r.id=j.run_id
            WHERE j.id=? AND j.status='running' AND j.lease_owner=?
            AND j.lease_generation=? AND j.lease_expires_at > ?
            AND r.status IN ('pending','running')""",
            (str(lease.job_id), lease.owner, lease.generation, to_utc_iso(self._clock.now_utc())),
        ).fetchone()
        if row is None:
            raise LeaseLost

    @staticmethod
    def _judge_snapshot(
        connection: sqlite3.Connection, profile_id: str | UUID | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        if profile_id is None:
            row = connection.execute(
                "SELECT * FROM judge_profiles WHERE is_global_default=1 AND active=1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM judge_profiles WHERE id=? AND active=1", (str(profile_id),)
            ).fetchone()
        return (None, None) if row is None else (row["id"], {key: row[key] for key in row.keys()})

    def get_run(self, run_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone()
            if row is None:
                raise NotFoundError("Run not found.")
            counts = connection.execute(
                "SELECT status,COUNT(*) count FROM run_jobs WHERE run_id=? GROUP BY status",
                (str(run_id),),
            ).fetchall()
            result = dict(row)
            result["job_counts"] = {x["status"]: x["count"] for x in counts}
            result["recovery_warning"] = bool(result["recovery_warning"])
            if result["recovery_warning"]:
                result["recovery_message"] = (
                    "Onderbroken providercalls kunnen opnieuw zijn uitgevoerd en "
                    "dubbele kosten veroorzaken."
                )
            return result
        finally:
            connection.close()
