"""Judge execution-profile contracts, SQLite persistence, and service."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evaluator.api.errors import ConflictError, JudgeParseError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.evaluators.core import EvaluationResult
from evaluator.judging.builtin import parse_judge_output
from evaluator.judging.extensions import (
    BUILTIN_JUDGE_ID,
    BUILTIN_JUDGE_VERSION,
    JudgeImplementationRegistry,
    JudgeRegistrationError,
)
from evaluator.judging.presets import RubricCriterion
from evaluator.logging_config import log_semantic_event
from evaluator.time import from_utc_iso, to_utc_iso, utc_now

# Preserve established structured-log component through judge consolidation.
_LOG = logging.getLogger("evaluator.judges")
_STORAGE_TEMPLATE = "preset-v1"
_STORAGE_TEMPLATE_VERSION = "1.0"
_STORAGE_THRESHOLD = Decimal("0")
_STORAGE_RUBRIC = "[]"


class JudgeProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    model_profile_id: UUID
    implementation_id: str = BUILTIN_JUDGE_ID
    implementation_version: str = BUILTIN_JUDGE_VERSION
    implementation_config: dict[str, Any] = Field(default_factory=dict)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    reasoning_value: str | None = Field(default=None, min_length=1, max_length=500)
    max_output_tokens: int | None = Field(default=None, gt=0)
    is_global_default: bool = False
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("reasoning_value")
    @classmethod
    def strip_reasoning_value(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def one_inference_override(self) -> JudgeProfileInput:
        if self.temperature is not None and self.reasoning_value is not None:
            raise ValueError("temperature and reasoning_value cannot both be configured")
        return self


class JudgeProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    model_profile_id: UUID | None = None
    implementation_id: str | None = None
    implementation_version: str | None = None
    implementation_config: dict[str, Any] | None = None
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    reasoning_value: str | None = Field(default=None, min_length=1, max_length=500)
    max_output_tokens: int | None = Field(default=None, gt=0)
    is_global_default: bool | None = None
    active: bool | None = None


class JudgeProfileRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    data: JudgeProfileInput
    created_at: datetime
    updated_at: datetime


class SQLiteJudgeProfileRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_all(self) -> list[JudgeProfileRecord]:
        connection = self._factory.connect()
        try:
            rows = connection.execute("SELECT * FROM judge_profiles ORDER BY name").fetchall()
        finally:
            connection.close()
        return [self._map(row) for row in rows]

    def get(self, profile_id: UUID) -> JudgeProfileRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM judge_profiles WHERE id=?", (str(profile_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map(row)

    def global_default(self) -> JudgeProfileRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM judge_profiles WHERE is_global_default=1 AND active=1"
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map(row)

    def golden_set_override(self, set_id: UUID) -> UUID | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT judge_profile_id FROM golden_sets WHERE id=?", (str(set_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None or row[0] is None else UUID(row[0])

    def set_golden_set_override(self, set_id: UUID, profile_id: UUID | None) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "UPDATE golden_sets SET judge_profile_id=?,updated_at=? WHERE id=?",
                    (str(profile_id) if profile_id else None, to_utc_iso(utc_now()), str(set_id)),
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    def save(self, record: JudgeProfileRecord) -> None:
        d = record.data
        connection = self._factory.connect()
        try:
            with transaction(connection):
                if d.is_global_default:
                    connection.execute(
                        "UPDATE judge_profiles SET is_global_default=0 WHERE id != ?",
                        (str(record.id),),
                    )
                connection.execute(
                    """INSERT INTO judge_profiles(
                    id,name,model_profile_id,prompt_template,template_version,threshold,rubric_json,
                    temperature,max_output_tokens,is_global_default,active,created_at,updated_at,
                    temperature_enabled,reasoning_value,implementation_id,implementation_version,
                    implementation_config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    model_profile_id=excluded.model_profile_id,
                    prompt_template=excluded.prompt_template,
                    template_version=excluded.template_version,threshold=excluded.threshold,
                    rubric_json=excluded.rubric_json,temperature=excluded.temperature,
                    max_output_tokens=excluded.max_output_tokens,is_global_default=excluded.is_global_default,
                    temperature_enabled=excluded.temperature_enabled,
                    reasoning_value=excluded.reasoning_value,
                    implementation_id=excluded.implementation_id,
                    implementation_version=excluded.implementation_version,
                    implementation_config_json=excluded.implementation_config_json,
                    active=excluded.active,updated_at=excluded.updated_at""",
                    (
                        str(record.id),
                        d.name,
                        str(d.model_profile_id),
                        _STORAGE_TEMPLATE,
                        _STORAGE_TEMPLATE_VERSION,
                        str(_STORAGE_THRESHOLD),
                        _STORAGE_RUBRIC,
                        str(d.temperature),
                        d.max_output_tokens,
                        int(d.is_global_default),
                        int(d.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                        int(d.temperature is not None),
                        d.reasoning_value,
                        d.implementation_id,
                        d.implementation_version,
                        json.dumps(d.implementation_config, separators=(",", ":"), sort_keys=True),
                    ),
                )
        finally:
            connection.close()

    def delete(self, profile_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "DELETE FROM judge_profiles WHERE id=?", (str(profile_id),)
                )
            return cursor.rowcount == 1
        finally:
            connection.close()

    @staticmethod
    def _map(row: sqlite3.Row) -> JudgeProfileRecord:
        return JudgeProfileRecord(
            id=UUID(row["id"]),
            data=JudgeProfileInput(
                name=row["name"],
                model_profile_id=UUID(row["model_profile_id"]),
                implementation_id=row["implementation_id"],
                implementation_version=row["implementation_version"],
                implementation_config=json.loads(row["implementation_config_json"]),
                temperature=Decimal(row["temperature"]) if row["temperature_enabled"] else None,
                reasoning_value=row["reasoning_value"],
                max_output_tokens=row["max_output_tokens"],
                is_global_default=bool(row["is_global_default"]),
                active=bool(row["active"]),
            ),
            created_at=from_utc_iso(row["created_at"]),
            updated_at=from_utc_iso(row["updated_at"]),
        )


class JudgeProfileService:
    def __init__(
        self,
        repository: SQLiteJudgeProfileRepository,
        registry: JudgeImplementationRegistry,
    ) -> None:
        self._repo = repository
        self._registry = registry

    def list_all(self) -> list[JudgeProfileRecord]:
        return self._repo.list_all()

    def get(self, profile_id: UUID) -> JudgeProfileRecord:
        value = self._repo.get(profile_id)
        if value is None:
            raise NotFoundError("Judge profile not found.")
        return value

    def create(self, payload: Any) -> JudgeProfileRecord:
        data = self._validate(JudgeProfileInput, payload)
        now = utc_now()
        record = JudgeProfileRecord(id=uuid4(), data=data, created_at=now, updated_at=now)
        self._save(record)
        log_semantic_event(_LOG, "judge_profile_created", object_id=record.id)
        return record

    def update(self, profile_id: UUID, payload: Any) -> JudgeProfileRecord:
        current = self.get(profile_id)
        patch = self._validate(JudgeProfilePatch, payload)
        data = JudgeProfileInput.model_validate(
            {**current.data.model_dump(), **patch.model_dump(exclude_unset=True)}
        )
        record = JudgeProfileRecord(
            id=current.id, data=data, created_at=current.created_at, updated_at=utc_now()
        )
        self._save(record)
        log_semantic_event(_LOG, "judge_profile_updated", object_id=record.id)
        return record

    def delete(self, profile_id: UUID) -> None:
        try:
            if not self._repo.delete(profile_id):
                raise NotFoundError("Judge profile not found.")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Judge profile is still in use.") from exc
        log_semantic_event(_LOG, "judge_profile_deleted", object_id=profile_id)

    def resolve(self, set_id: UUID, run_override: UUID | None = None) -> JudgeProfileRecord:
        selected = run_override or self._repo.golden_set_override(set_id)
        result = self._repo.get(selected) if selected else self._repo.global_default()
        if result is None or not result.data.active:
            raise ValidationApiError("No active judge profile is available.")
        return result

    def golden_set_override(self, set_id: UUID) -> UUID | None:
        return self._repo.golden_set_override(set_id)

    def set_golden_set_override(self, set_id: UUID, profile_id: UUID | None) -> None:
        if profile_id is not None:
            profile = self.get(profile_id)
            if not profile.data.active:
                raise ValidationApiError("Judge profile is not active.")
        if not self._repo.set_golden_set_override(set_id, profile_id):
            raise NotFoundError("Golden set not found.")
        log_semantic_event(
            _LOG,
            "golden_set_judge_changed",
            object_id=set_id,
            profile_id=profile_id,
        )

    def evaluate_with_retry(
        self, call: Callable[[], str], rubric: list[RubricCriterion], threshold: Decimal
    ) -> EvaluationResult:
        for attempt in range(2):
            try:
                return parse_judge_output(call(), rubric, threshold)
            except JudgeParseError:
                if attempt == 1:
                    raise
        raise AssertionError

    def _save(self, record: JudgeProfileRecord) -> None:
        try:
            configuration = self._registry.validate_configuration(
                record.data.implementation_id,
                record.data.implementation_version,
                record.data.implementation_config,
            )
            record = record.model_copy(
                update={
                    "data": record.data.model_copy(update={"implementation_config": configuration})
                }
            )
            self._repo.save(record)
        except JudgeRegistrationError as exc:
            raise ValidationApiError(str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Judge profile conflicts with existing data.") from exc

    @staticmethod
    def _validate(model: type[JudgeProfileInput] | type[JudgeProfilePatch], payload: Any) -> Any:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc


def judge_profile_to_dict(record: JudgeProfileRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        **record.data.model_dump(mode="json"),
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }
