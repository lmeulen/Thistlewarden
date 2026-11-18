"""SQLite persistence and lifecycle service for versioned judge definitions."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.judging.definitions import (
    METRIC_CATALOG,
    JudgeDefinitionInput,
    JudgeDefinitionVersion,
    semantic_hash,
)
from evaluator.time import from_utc_iso, to_utc_iso, utc_now


class JudgeDefinitionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    origin: str
    lifecycle: str
    draft: JudgeDefinitionInput | None
    created_at: datetime
    updated_at: datetime


class SQLiteJudgeDefinitionRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_all(self) -> list[JudgeDefinitionRecord]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM judge_definitions ORDER BY name COLLATE NOCASE"
            ).fetchall()
        finally:
            connection.close()
        return [self._record(row) for row in rows]

    def list_published_versions(self) -> list[JudgeDefinitionVersion]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT d.id,d.origin,d.lifecycle,v.version,v.data_json,v.semantic_hash
                FROM judge_definition_versions v JOIN judge_definitions d ON d.id=v.definition_id
                WHERE d.lifecycle='published' ORDER BY d.name COLLATE NOCASE,v.version"""
            ).fetchall()
        finally:
            connection.close()
        return [
            JudgeDefinitionVersion(
                definition_id=row["id"],
                version=row["version"],
                origin=row["origin"],
                lifecycle=row["lifecycle"],
                data=JudgeDefinitionInput.model_validate_json(row["data_json"]),
                semantic_hash=row["semantic_hash"],
            )
            for row in rows
        ]

    def get(self, definition_id: str) -> JudgeDefinitionRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM judge_definitions WHERE id=?", (definition_id,)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._record(row)

    def list_versions(self, definition_id: str) -> list[JudgeDefinitionVersion]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """SELECT d.id,d.origin,d.lifecycle,v.version,v.data_json,v.semantic_hash
                FROM judge_definition_versions v JOIN judge_definitions d ON d.id=v.definition_id
                WHERE v.definition_id=?""",
                (definition_id,),
            ).fetchall()
        finally:
            connection.close()
        values = [self._version(row) for row in rows]
        return sorted(values, key=lambda item: _version_key(item.version))

    def get_version(self, definition_id: str, version: str) -> JudgeDefinitionVersion | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """SELECT d.id,d.origin,d.lifecycle,v.version,v.data_json,v.semantic_hash
                FROM judge_definition_versions v JOIN judge_definitions d ON d.id=v.definition_id
                WHERE v.definition_id=? AND v.version=?""",
                (definition_id, version),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self._version(row)

    @staticmethod
    def _version(row: sqlite3.Row) -> JudgeDefinitionVersion:
        return JudgeDefinitionVersion(
            definition_id=row["id"],
            version=row["version"],
            origin=row["origin"],
            lifecycle=row["lifecycle"],
            data=JudgeDefinitionInput.model_validate_json(row["data_json"]),
            semantic_hash=row["semantic_hash"],
        )

    def save(self, record: JudgeDefinitionRecord) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO judge_definitions(
                    id,name,origin,lifecycle,draft_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,lifecycle=excluded.lifecycle,draft_json=excluded.draft_json,
                    updated_at=excluded.updated_at""",
                    (
                        record.id,
                        record.name,
                        record.origin,
                        record.lifecycle,
                        record.draft.model_dump_json() if record.draft is not None else None,
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
        finally:
            connection.close()

    def publish(self, record: JudgeDefinitionRecord, version: str) -> JudgeDefinitionVersion:
        if record.draft is None:
            raise ValueError("definition has no draft")
        published = JudgeDefinitionVersion(
            definition_id=record.id,
            version=version,
            origin="local",
            lifecycle="published",
            data=record.draft,
            semantic_hash=semantic_hash(record.draft),
        )
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO judge_definition_versions(
                    definition_id,version,data_json,semantic_hash,published_at)
                    VALUES(?,?,?,?,?)""",
                    (
                        published.definition_id,
                        published.version,
                        published.data.model_dump_json(),
                        published.semantic_hash,
                        to_utc_iso(utc_now()),
                    ),
                )
                connection.execute(
                    "UPDATE judge_definitions SET lifecycle='published',updated_at=? WHERE id=?",
                    (to_utc_iso(utc_now()), record.id),
                )
        finally:
            connection.close()
        return published

    @staticmethod
    def _record(row: sqlite3.Row) -> JudgeDefinitionRecord:
        return JudgeDefinitionRecord(
            id=row["id"],
            name=row["name"],
            origin=row["origin"],
            lifecycle=row["lifecycle"],
            draft=(
                JudgeDefinitionInput.model_validate_json(row["draft_json"])
                if row["draft_json"]
                else None
            ),
            created_at=from_utc_iso(row["created_at"]),
            updated_at=from_utc_iso(row["updated_at"]),
        )


class JudgeDefinitionService:
    def __init__(self, repository: SQLiteJudgeDefinitionRepository) -> None:
        self._repository = repository

    def list_all(self) -> list[JudgeDefinitionRecord]:
        return self._repository.list_all()

    def list_published_versions(self) -> list[JudgeDefinitionVersion]:
        return self._repository.list_published_versions()

    def get(self, definition_id: str) -> JudgeDefinitionRecord:
        result = self._repository.get(definition_id)
        if result is None:
            raise NotFoundError("Judge not found.")
        return result

    def list_versions(self, definition_id: str) -> list[JudgeDefinitionVersion]:
        self.get(definition_id)
        return self._repository.list_versions(definition_id)

    def next_version(self, definition_id: str) -> str:
        versions = self.list_versions(definition_id)
        if not versions:
            return "1.0"
        parts = versions[-1].version.split(".")
        return f"{parts[0]}.{int(parts[1]) + 1}"

    def get_version(self, definition_id: str, version: str) -> JudgeDefinitionVersion:
        result = self._repository.get_version(definition_id, version)
        if result is None:
            raise NotFoundError("Judge version not found.")
        return result

    def create(self, payload: Any) -> JudgeDefinitionRecord:
        draft = self._validate(payload)
        now = utc_now()
        record = JudgeDefinitionRecord(
            id=f"judge-{uuid4().hex}",
            name=draft.name,
            origin="local",
            lifecycle="draft",
            draft=draft,
            created_at=now,
            updated_at=now,
        )
        self._repository.save(record)
        return record

    def update_draft(self, definition_id: str, payload: Any) -> JudgeDefinitionRecord:
        current = self.get(definition_id)
        if current.origin != "local" or current.lifecycle == "archived":
            raise ConflictError("Only active local judge drafts can be changed.")
        draft = self._validate(payload)
        updated = current.model_copy(
            update={"name": draft.name, "draft": draft, "updated_at": utc_now()}
        )
        self._repository.save(updated)
        return updated

    def publish(self, definition_id: str, version: str = "1.0") -> JudgeDefinitionVersion:
        current = self.get(definition_id)
        if current.origin != "local" or current.draft is None:
            raise ConflictError("Only local judge drafts can be published.")
        try:
            return self._repository.publish(current, version)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        except sqlite3.IntegrityError as exc:
            if self._repository.get_version(definition_id, version) is not None:
                raise ConflictError("Judge version already exists.") from exc
            raise ConflictError(
                "Published Judge semantics are unchanged. Update the draft before publishing."
            ) from exc

    @staticmethod
    def _validate(payload: Any) -> JudgeDefinitionInput:
        try:
            value = JudgeDefinitionInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        removed = sorted({metric.metric_id for metric in value.metrics} - set(METRIC_CATALOG))
        if removed:
            raise ValidationApiError(
                f"Unsupported metrics: {', '.join(removed)}. Use one of the 10 available metrics."
            )
        return value


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def judge_definition_to_dict(record: JudgeDefinitionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "origin": record.origin,
        "lifecycle": record.lifecycle,
        "draft": record.draft.model_dump(mode="json") if record.draft else None,
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }


def judge_definition_version_to_dict(value: JudgeDefinitionVersion) -> dict[str, Any]:
    return value.model_dump(mode="json")
