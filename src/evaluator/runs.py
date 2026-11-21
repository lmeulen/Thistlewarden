"""Immutable run snapshots and durable fenced SQLite job queue."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.api.errors import NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.evaluators.contracts import LlmJudgeConfig
from evaluator.gates import QualityGates
from evaluator.judging.definitions import JudgeDefinitionInput
from evaluator.judging.extensions import JudgeImplementationRegistry, JudgeImplementationUnavailable
from evaluator.logging_config import log_semantic_event
from evaluator.time import Clock, SystemClock, to_utc_iso

_LOG = logging.getLogger(__name__)
_CASE_SNAPSHOT_SCHEMA_VERSION = "2.0"


def normalize_case_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map legacy single-prompt snapshots to canonical ordered user turns in memory."""

    value = dict(snapshot)
    prompts = value.get("user_prompts")
    if prompts is None:
        prompt = value.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("case snapshot must contain a prompt or user_prompts")
        prompts = [prompt]
    if (
        not isinstance(prompts, list)
        or not prompts
        or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts)
    ):
        raise ValueError("case snapshot user_prompts must be a non-empty list of strings")
    value["user_prompts"] = list(prompts)
    value["prompt"] = prompts[0]
    return value


class LeaseLost(RuntimeError):
    """Worker fencing token no longer owns a live lease."""


class IncompleteTargetTurn(RuntimeError):
    """A streamed target response lacks a durable completion checkpoint."""


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
        judge_registry: JudgeImplementationRegistry | None = None,
    ) -> None:
        self._factory, self._clock = factory, clock or SystemClock()
        self._lease_seconds, self._max_profiles = lease_seconds, max_profiles
        self._judge_registry = judge_registry

    def rename(self, run_id: UUID, name: str) -> None:
        normalized = name.strip()
        if not normalized or len(normalized) > 200:
            raise ValidationApiError("Run name must contain between 1 and 200 characters.")
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                cursor = connection.execute(
                    "UPDATE runs SET name=? WHERE id=?", (normalized, str(run_id))
                )
                if cursor.rowcount != 1:
                    raise NotFoundError("Run not found.")
        finally:
            connection.close()

    def create_run(
        self,
        payload: Any,
        *,
        expected_rerun_scope: tuple[UUID, str] | None = None,
    ) -> UUID:
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
                if expected_rerun_scope is not None:
                    source_run_id, expected_fingerprint = expected_rerun_scope
                    scope = self._rerun_failures_scope(connection, source_run_id)
                    if scope["fingerprint"] != expected_fingerprint:
                        raise ValidationApiError(
                            "Rerun review is stale. Review current configuration again."
                        )
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
                        e.api_key_header,e.api_format FROM model_profiles m JOIN endpoints e
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
                has_llm_judge_cases = any(row["evaluation_method"] == "llm_judge" for row in cases)
                if has_llm_judge_cases and judge_snapshot[1] is None:
                    raise ValidationApiError(
                        "LLM-judge test cases require an active Judge profile. "
                        "Select one for this run, golden set, or as global default."
                    )
                if judge_snapshot[1] is not None and self._judge_registry is not None:
                    try:
                        implementation_id = judge_snapshot[1]["implementation_id"]
                        implementation_version = judge_snapshot[1]["implementation_version"]
                        validated_config = self._judge_registry.validate_configuration(
                            implementation_id,
                            implementation_version,
                            judge_snapshot[1]["implementation_config"],
                        )
                    except JudgeImplementationUnavailable as exc:
                        raise ValidationApiError(
                            "Selected judge implementation is unavailable."
                        ) from exc
                    judge_snapshot[1]["implementation_config"] = validated_config
                if judge_snapshot[1] is not None and all(
                    str(row["id"]) != judge_snapshot[1]["model_profile_id"] for row in models
                ):
                    judge_model = connection.execute(
                        """SELECT m.*,e.completions_url,e.name AS endpoint_name,
                        e.api_key_header,e.api_format FROM model_profiles m JOIN endpoints e
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
                        "snapshot_schema_version": _CASE_SNAPSHOT_SCHEMA_VERSION,
                        "case_id": row["case_id"],
                        "user_prompts": json.loads(row["user_prompts_json"]),
                        **{
                            key: row[key]
                            for key in (
                                "system_prompt",
                                "expected_answer",
                                "evaluation_method",
                                "evaluator_config_json",
                                "category",
                                "tags_json",
                                "weight",
                            )
                        },
                    }
                    if row["evaluation_method"] == "llm_judge":
                        config = LlmJudgeConfig.model_validate(
                            json.loads(row["evaluator_config_json"])
                        )
                        if config.judge_definition_id is not None:
                            snapshot["judge_definition"] = self._judge_definition_snapshot(
                                connection,
                                config.judge_definition_id,
                                config.judge_definition_version,
                                config.expected_value,
                                config.context,
                            )
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
        log_semantic_event(
            _LOG,
            "run_created",
            object_id=run_id,
            case_count=len(case_ids),
            model_count=len(model_ids),
            total=len(case_ids) * len(model_ids),
        )
        return run_id

    def recover_expired(self) -> dict[str, int]:
        """Expose expired interrupted work; generation changes only on next claim."""
        now = to_utc_iso(self._clock.now_utc())
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                rows = connection.execute(
                    """SELECT j.id,j.run_id FROM run_jobs j
                    JOIN runs r ON r.id=j.run_id
                    WHERE j.status='running' AND j.lease_expires_at <= ?
                    AND r.status IN ('pending','running')""",
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

    def turn_state(self, lease: JobLease) -> list[dict[str, Any]]:
        """Return durable turn checkpoints; reject a recovery that might replay a stream."""
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                rows = connection.execute(
                    "SELECT * FROM run_job_turns WHERE job_id=? ORDER BY turn_index",
                    (str(lease.job_id),),
                ).fetchall()
                result = [dict(row) for row in rows]
                if any(row["status"] == "started" for row in result):
                    raise IncompleteTargetTurn("incomplete target turn cannot be replayed")
                return result
        finally:
            connection.close()

    def start_turn(self, lease: JobLease, turn_index: int, user_prompt: str) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                connection.execute(
                    """INSERT INTO run_job_turns(
                    id,job_id,turn_index,lease_generation,user_prompt,status,started_at
                    ) VALUES (?,?,?,?,?,'started',?)""",
                    (
                        str(uuid4()),
                        str(lease.job_id),
                        turn_index,
                        lease.generation,
                        user_prompt,
                        to_utc_iso(self._clock.now_utc()),
                    ),
                )
        finally:
            connection.close()

    def complete_turn(
        self,
        lease: JobLease,
        turn_index: int,
        *,
        assistant_response: str,
        metrics: dict[str, Any],
        attempts: tuple[dict[str, Any], ...],
        provider_evidence: dict[str, Any],
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                self._assert_lease(connection, lease)
                cursor = connection.execute(
                    """UPDATE run_job_turns SET status='completed',assistant_response=?,
                    metrics_json=?,attempts_json=?,provider_evidence_json=?,completed_at=?
                    WHERE job_id=? AND turn_index=? AND status='started' AND lease_generation=?""",
                    (
                        assistant_response,
                        json.dumps(metrics, separators=(",", ":")),
                        json.dumps(attempts, separators=(",", ":")),
                        json.dumps(provider_evidence, separators=(",", ":")),
                        to_utc_iso(self._clock.now_utc()),
                        str(lease.job_id),
                        turn_index,
                        lease.generation,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLost
        finally:
            connection.close()

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

    def fail(
        self,
        lease: JobLease,
        error_code: str,
        metadata: dict[str, Any],
        *,
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
                        "error",
                        None,
                        "1",
                        json.dumps(metadata, separators=(",", ":")),
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
            with transaction(connection, mode="IMMEDIATE"):
                cursor = connection.execute(
                    "UPDATE runs SET status='cancelled',cancelled_at=? WHERE id=? "
                    "AND status IN ('pending','running')",
                    (now, str(run_id)),
                )
                if cursor.rowcount != 1:
                    raise ValidationApiError("Run cannot be cancelled.")
                connection.execute(
                    """UPDATE run_jobs
                    SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                    WHERE run_id=? AND status IN ('pending','running')""",
                    (now, str(run_id)),
                )
                counts = connection.execute(
                    "SELECT COUNT(*),SUM(CASE WHEN status NOT IN "
                    "('pending','running') THEN 1 ELSE 0 END) FROM run_jobs WHERE run_id=?",
                    (str(run_id),),
                ).fetchone()
        finally:
            connection.close()
        log_semantic_event(
            _LOG, "run_cancelled", object_id=run_id, total=counts[0], done=counts[1] or 0
        )

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

    def cancel_runs_with_unavailable_judges(self) -> int:
        """Terminally cancel active work whose snapshotted implementation is absent."""

        if self._judge_registry is None:
            return 0
        available = set(self._judge_registry.identities())
        now = to_utc_iso(self._clock.now_utc())
        connection = self._factory.connect()
        try:
            with transaction(connection, mode="IMMEDIATE"):
                rows = connection.execute(
                    """SELECT id,judge_snapshot_json FROM runs
                    WHERE status IN ('pending','running') AND judge_snapshot_json IS NOT NULL"""
                ).fetchall()
                run_ids = []
                for row in rows:
                    snapshot = json.loads(row["judge_snapshot_json"])
                    identity = (
                        snapshot.get("implementation_id", "openai-compatible"),
                        snapshot.get("implementation_version", "1.0"),
                    )
                    if identity not in available:
                        run_ids.append(row["id"])
                if run_ids:
                    placeholders = ",".join("?" for _ in run_ids)
                    connection.execute(
                        f"UPDATE run_jobs SET status='cancelled',lease_owner=NULL,"
                        f"lease_expires_at=NULL,updated_at=? WHERE run_id IN ({placeholders}) "
                        "AND status IN ('pending','running')",
                        (now, *run_ids),
                    )
                    connection.execute(
                        f"UPDATE runs SET status='cancelled',cancelled_at=?,"
                        f"note=COALESCE(note||'\n','')||? WHERE id IN ({placeholders})",
                        (
                            now,
                            "Cancelled: snapshotted judge implementation is unavailable.",
                            *run_ids,
                        ),
                    )
        finally:
            connection.close()
        for run_id in run_ids:
            log_semantic_event(
                _LOG,
                "run_cancelled_missing_judge_implementation",
                object_id=run_id,
            )
        return len(run_ids)

    def rerun_failures_preview(self, run_id: UUID) -> dict[str, Any]:
        connection = self._factory.connect()
        try:
            return self._rerun_failures_scope(connection, run_id)
        finally:
            connection.close()

    def rerun_failures(self, run_id: UUID, name: str, fingerprint: str) -> UUID:
        preview = self.rerun_failures_preview(run_id)
        payload: dict[str, Any] = {
            "name": name,
            "golden_set_id": preview["golden_set_id"],
            "model_profile_ids": preview["model_profile_ids"],
        }
        if preview["judge_profile_id"]:
            payload["judge_profile_id"] = preview["judge_profile_id"]
        new_id = self.create_run(payload, expected_rerun_scope=(run_id, fingerprint))
        log_semantic_event(
            _LOG,
            "run_models_with_errors_rerun",
            object_id=new_id,
            source=run_id,
            count=len(preview["model_profile_ids"]),
        )
        return new_id

    def _rerun_failures_scope(self, connection: sqlite3.Connection, run_id: UUID) -> dict[str, Any]:
        run = connection.execute(
            "SELECT golden_set_id,judge_profile_id FROM runs WHERE id=?", (str(run_id),)
        ).fetchone()
        failed_models = connection.execute(
            """SELECT DISTINCT rms.source_profile_id,m.display_name,m.model_id
            FROM run_jobs j JOIN run_model_snapshots rms ON rms.id=j.model_snapshot_id
            JOIN model_profiles m ON m.id=rms.source_profile_id
            WHERE j.run_id=? AND j.status='failed' ORDER BY rms.source_profile_id""",
            (str(run_id),),
        ).fetchall()
        if run is None or not failed_models:
            raise ValidationApiError("No errored jobs found.")
        golden_set_id = str(run["golden_set_id"])
        cases = connection.execute(
            "SELECT * FROM golden_set_items WHERE golden_set_id=? "
            "AND enabled=1 ORDER BY sort_order,id",
            (golden_set_id,),
        ).fetchall()
        placeholders = ",".join("?" for _ in failed_models)
        models = connection.execute(
            f"""SELECT m.*,e.completions_url,e.name AS endpoint_name,e.api_key_header,e.api_format,
            e.active AS endpoint_active FROM model_profiles m JOIN endpoints e ON e.id=m.endpoint_id
            WHERE m.id IN ({placeholders}) ORDER BY m.id""",  # noqa: S608
            tuple(row["source_profile_id"] for row in failed_models),
        ).fetchall()
        set_row = connection.execute(
            "SELECT * FROM golden_sets WHERE id=?", (golden_set_id,)
        ).fetchone()
        if set_row is None or not cases or len(models) != len(failed_models):
            raise ValidationApiError("Current configuration cannot create this rerun.")
        judge_profiles = connection.execute("SELECT * FROM judge_profiles ORDER BY id").fetchall()
        serializable = {
            "source_run_id": str(run_id),
            "golden_set": dict(set_row),
            "cases": [dict(row) for row in cases],
            "models": [dict(row) for row in models],
            # Include all judge profiles: current selection can fall back to global default.
            "judge_profiles": [dict(row) for row in judge_profiles],
            "judge_profile_id": run["judge_profile_id"],
        }
        fingerprint = hashlib.sha256(
            json.dumps(serializable, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        model_ids = [str(row["source_profile_id"]) for row in failed_models]
        active_cases = len(cases)
        target_calls_per_model = sum(len(json.loads(row["user_prompts_json"])) for row in cases)
        llm_cases = sum(row["evaluation_method"] == "llm_judge" for row in cases)
        return {
            "fingerprint": fingerprint,
            "golden_set_id": golden_set_id,
            "judge_profile_id": str(run["judge_profile_id"] or ""),
            "model_profile_ids": model_ids,
            "models": [
                {
                    "id": str(row["source_profile_id"]),
                    "name": row["display_name"],
                    "model_identifier": row["model_id"],
                }
                for row in failed_models
            ],
            "active_cases": active_cases,
            "job_count": active_cases * len(model_ids),
            "target_call_count": target_calls_per_model * len(model_ids),
            "judge_call_count": llm_cases * len(model_ids),
        }

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
        return normalize_case_snapshot(value) if kind == "case" else value

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
    def _judge_definition_snapshot(
        connection: sqlite3.Connection,
        definition_id: str,
        version: str | None,
        expected_value: str | None,
        context: str | None,
    ) -> dict[str, Any]:
        if version is None:
            raise ValidationApiError("Judge definition version is required.")
        row = connection.execute(
            """SELECT data_json,semantic_hash FROM judge_definition_versions
            WHERE definition_id=? AND version=?""",
            (definition_id, version),
        ).fetchone()
        if row is None:
            raise ValidationApiError("Published judge definition version not found.")
        data = JudgeDefinitionInput.model_validate_json(row["data_json"])
        if data.requires_expected and expected_value is None:
            raise ValidationApiError("Judge requires an expected answer.")
        if data.requires_context and context is None:
            raise ValidationApiError("Judge requires context.")
        return {
            "id": definition_id,
            "version": version,
            "semantic_hash": row["semantic_hash"],
            "data": data.model_dump(mode="json"),
        }

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
        if row is None:
            return None, None
        return row["id"], {
            key: row[key]
            for key in (
                "id",
                "name",
                "model_profile_id",
                "temperature",
                "temperature_enabled",
                "reasoning_value",
                "max_output_tokens",
                "implementation_id",
                "implementation_version",
                "is_global_default",
                "active",
                "created_at",
                "updated_at",
            )
        } | {"implementation_config": json.loads(row["implementation_config_json"])}

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
                    "Interrupted provider calls may have been repeated and caused duplicate costs."
                )
            return result
        finally:
            connection.close()
