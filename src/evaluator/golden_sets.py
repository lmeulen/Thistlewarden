"""Golden sets, test cases, evaluator configuration, and persistence."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from evaluator.api.errors import ConflictError, ErrorDetail, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_semantic_event
from evaluator.time import to_utc_iso, utc_now

_LOG = logging.getLogger(__name__)


class ExactMatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["exact_match"] = "exact_match"
    # Optional only to support legacy records whose value remains in expected_answer.
    expected_value: str | None = None
    trim: bool = True
    case_sensitive: bool = True
    normalize_newlines: bool = True
    collapse_whitespace: bool = False


class ContainsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["contains"] = "contains"
    expected_fragments: list[str] = Field(min_length=1)
    match_mode: Literal["all", "any"] = "all"
    case_sensitive: bool = True
    normalize_newlines: bool = True
    collapse_whitespace: bool = False

    @field_validator("expected_fragments")
    @classmethod
    def fragments_not_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("expected fragments must not be blank")
        return values


class RegexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["regex"] = "regex"
    pattern: str = Field(min_length=1, max_length=100_000)
    full_match: bool = False
    case_sensitive: bool = True
    multiline: bool = False
    dot_all: bool = False

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc.msg}") from exc
        return value


class NumericConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["numeric"] = "numeric"
    expected_value: Decimal
    absolute_tolerance: Decimal | None = Field(default=None, ge=0)
    relative_tolerance_percent: Decimal | None = Field(default=None, ge=0)
    extraction: Literal["entire_answer", "first_number"] = "entire_answer"
    decimal_separator: Literal[".", ","] = "."
    allow_thousands_separator: bool = False


class JsonSchemaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["json_schema"] = "json_schema"
    schema_: dict[str, Any] = Field(alias="schema")
    allow_markdown_code_fence: bool = True
    schema_draft: Literal["2020-12"] = "2020-12"
    expected_value: Any | None = None

    @field_validator("schema_")
    @classmethod
    def valid_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON Schema: {exc.message}") from exc
        return value


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=100)
    description: str = Field(min_length=1, max_length=20_000)
    weight: Decimal = Field(default=Decimal("1"), ge=0)


class LlmJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["llm_judge"] = "llm_judge"
    expected_value: str | None = None
    rubric: list[RubricCriterion] | None = None
    rubric_mode: Literal["augment", "replace"] = "augment"
    threshold: Decimal | None = Field(default=None, ge=0, le=1)
    context: str | None = None

    @field_validator("rubric")
    @classmethod
    def unique_criterion_ids(
        cls, value: list[RubricCriterion] | None
    ) -> list[RubricCriterion] | None:
        if value is not None and len({item.id for item in value}) != len(value):
            raise ValueError("rubric criterion IDs must be unique")
        return value

    @model_validator(mode="after")
    def active_replacement_rubric(self) -> LlmJudgeConfig:
        if self.rubric_mode == "replace" and self.rubric and not any(
            item.weight > 0 for item in self.rubric
        ):
            raise ValueError("replacement rubric requires a criterion with positive weight")
        return self


EvaluatorConfig = Annotated[
    ExactMatchConfig
    | ContainsConfig
    | RegexConfig
    | NumericConfig
    | JsonSchemaConfig
    | LlmJudgeConfig,
    Field(discriminator="evaluation_method"),
]
_CONFIG_ADAPTER: TypeAdapter[EvaluatorConfig] = TypeAdapter(EvaluatorConfig)
_EVALUATOR_CONFIG_EXAMPLES: dict[str, dict[str, Any]] = {
    "exact_match": {
        "expected_value": "expected text",
        "trim": True,
        "case_sensitive": True,
        "normalize_newlines": True,
        "collapse_whitespace": False,
    },
    "contains": {
        "expected_fragments": ["required text"],
        "match_mode": "all",
        "case_sensitive": True,
        "normalize_newlines": True,
        "collapse_whitespace": False,
    },
    "regex": {
        "pattern": "^required text$",
        "full_match": False,
        "case_sensitive": True,
        "multiline": False,
        "dot_all": False,
    },
    "numeric": {
        "expected_value": "42",
        "absolute_tolerance": "0.1",
        "relative_tolerance_percent": None,
        "extraction": "entire_answer",
        "decimal_separator": ".",
        "allow_thousands_separator": False,
    },
    "json_schema": {
        "schema": {"type": "object", "required": ["answer"]},
        "allow_markdown_code_fence": True,
        "schema_draft": "2020-12",
    },
    "llm_judge": {
        "expected_value": "expected answer or reference text",
        "rubric": [
            {
                "id": "factual_correctness",
                "description": "Claims and conclusions are factually correct.",
                "weight": "2",
            },
            {
                "id": "completeness",
                "description": "Answer covers all requested points.",
                "weight": "1",
            },
            {
                "id": "relevance",
                "description": "Answer directly addresses request without irrelevant content.",
                "weight": "1",
            },
            {
                "id": "instruction_compliance",
                "description": "Answer follows instructions, constraints, format, and language.",
                "weight": "1",
            },
            {
                "id": "consistency",
                "description": "Answer contains no internal contradictions.",
                "weight": "1",
            },
            {
                "id": "clarity",
                "description": "Answer is understandable, structured, and unambiguous.",
                "weight": "1",
            },
            {
                "id": "groundedness",
                "description": "Claims are supported by supplied context or reference material.",
                "weight": "0",
            },
            {
                "id": "safety",
                "description": "Answer avoids harmful, prohibited, or inappropriate guidance.",
                "weight": "0",
            },
        ],
        "rubric_mode": "replace",
        "threshold": "0.8",
        "context": None,
    },
}


def evaluator_config_examples() -> dict[str, dict[str, Any]]:
    """Return editable examples without exposing shared configuration state."""

    return deepcopy(_EVALUATOR_CONFIG_EXAMPLES)


def validate_evaluator_config(method: str, config: Any) -> EvaluatorConfig:
    if not isinstance(config, dict):
        raise ValueError("evaluator_config must be an object")
    return _CONFIG_ADAPTER.validate_python({**config, "evaluation_method": method})


def _validate_tags(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value or len(value) > 100 for value in cleaned):
        raise ValueError("tags must be non-blank and at most 100 characters")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("tags must be unique")
    return cleaned


class GoldenSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=100_000)
    version: str = Field(default="1.0", min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    active: bool = True

    @field_validator("name", "version")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        return _validate_tags(values)


class GoldenSetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=100_000)
    version: str | None = Field(default=None, min_length=1, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=100)
    active: bool | None = None


class GoldenSetItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    expected_answer: str | None = None
    evaluation_method: Literal[
        "exact_match", "contains", "regex", "numeric", "json_schema", "llm_judge"
    ]
    evaluator_config: dict[str, Any] = Field(default_factory=dict)
    category: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)
    weight: Decimal = Field(default=Decimal("1.0"), gt=0)
    enabled: bool = True
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("case_id", "prompt")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if len(value) <= 200 else value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        return _validate_tags(values)

    @model_validator(mode="after")
    def validate_method_requirements(self) -> GoldenSetItemInput:
        config = validate_evaluator_config(self.evaluation_method, self.evaluator_config)
        normalized_config = config.model_dump(
            mode="json", by_alias=True, exclude={"evaluation_method"}
        )
        if (
            self.evaluation_method == "json_schema"
            and "expected_value" not in self.evaluator_config
        ):
            normalized_config.pop("expected_value", None)
        if self.evaluation_method == "llm_judge" and "expected_value" not in self.evaluator_config:
            normalized_config.pop("expected_value", None)
        if self.evaluation_method == "exact_match":
            exact = config
            assert isinstance(exact, ExactMatchConfig)
            expected_value = exact.expected_value
            if expected_value is None:
                # Legacy records and snapshots stored exact-match values separately.
                expected_value = self.expected_answer
            if expected_value is None or not expected_value.strip():
                raise ValueError("expected_value is required for exact_match")
            normalized_config["expected_value"] = expected_value
            # Keep expected references in method configuration, not legacy shared column.
            self.expected_answer = None
        self.evaluator_config = normalized_config
        if self.evaluation_method == "llm_judge":
            judge = config
            assert isinstance(judge, LlmJudgeConfig)
            expected_value = judge.expected_value
            if expected_value is None:
                # Legacy records and snapshots stored judge reference text separately.
                expected_value = self.expected_answer
            if expected_value is not None:
                if not expected_value.strip():
                    raise ValueError("expected_value must not be blank")
                normalized_config["expected_value"] = expected_value
            elif not judge.rubric:
                raise ValueError("llm_judge requires expected_value or an item rubric")
            self.expected_answer = None
            self.evaluator_config = normalized_config
        return self


class GoldenSetItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1)
    system_prompt: str | None = None
    expected_answer: str | None = None
    evaluation_method: (
        Literal["exact_match", "contains", "regex", "numeric", "json_schema", "llm_judge"] | None
    ) = None
    evaluator_config: dict[str, Any] | None = None
    category: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=100)
    weight: Decimal | None = Field(default=None, gt=0)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


class GoldenSetRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    data: GoldenSetInput
    created_at: datetime
    updated_at: datetime


class GoldenSetItemRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    golden_set_id: UUID
    data: GoldenSetItemInput
    created_at: datetime
    updated_at: datetime


class GoldenSetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_items: int = Field(gt=0)
    max_prompt_chars: int = Field(gt=0)
    max_system_prompt_chars: int = Field(gt=0)
    max_expected_answer_chars: int = Field(gt=0)
    max_judge_rubric_context_chars: int = Field(gt=0)
    max_import_file_bytes: int = Field(gt=0)


class SQLiteGoldenSetRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_sets(self, *, page: int, per_page: int) -> tuple[list[GoldenSetRecord], int]:
        connection = self._factory.connect()
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM golden_sets").fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM golden_sets ORDER BY name LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_set(row) for row in rows], total

    def get_set(self, set_id: UUID) -> GoldenSetRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM golden_sets WHERE id = ?", (str(set_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map_set(row)

    def save_set(self, record: GoldenSetRecord) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    description=excluded.description,version=excluded.version,
                    tags_json=excluded.tags_json,active=excluded.active,
                    updated_at=excluded.updated_at""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
        finally:
            connection.close()

    def import_set(self, record: GoldenSetRecord, items: list[GoldenSetItemRecord]) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
                connection.executemany(
                    "INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            str(item.id),
                            str(record.id),
                            item.data.case_id,
                            item.data.prompt,
                            item.data.system_prompt,
                            item.data.expected_answer,
                            item.data.evaluation_method,
                            json.dumps(
                                item.data.evaluator_config, sort_keys=True, separators=(",", ":")
                            ),
                            item.data.category,
                            json.dumps(item.data.tags),
                            str(item.data.weight),
                            int(item.data.enabled),
                            item.data.sort_order,
                            to_utc_iso(item.created_at),
                            to_utc_iso(item.updated_at),
                        )
                        for item in items
                    ],
                )
        finally:
            connection.close()

    def duplicate_set(
        self,
        source_id: UUID,
        record: GoldenSetRecord,
        items: list[GoldenSetItemRecord],
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                source = connection.execute(
                    "SELECT judge_profile_id,quality_gates_json FROM golden_sets WHERE id=?",
                    (str(source_id),),
                ).fetchone()
                if source is None:
                    raise NotFoundError("Golden set not found.")
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at,
                    judge_profile_id,quality_gates_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                        source["judge_profile_id"],
                        source["quality_gates_json"],
                    ),
                )
                connection.executemany(
                    "INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            str(item.id),
                            str(record.id),
                            item.data.case_id,
                            item.data.prompt,
                            item.data.system_prompt,
                            item.data.expected_answer,
                            item.data.evaluation_method,
                            json.dumps(
                                item.data.evaluator_config,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            item.data.category,
                            json.dumps(item.data.tags),
                            str(item.data.weight),
                            int(item.data.enabled),
                            item.data.sort_order,
                            to_utc_iso(item.created_at),
                            to_utc_iso(item.updated_at),
                        )
                        for item in items
                    ],
                )
        finally:
            connection.close()

    def delete_set(self, set_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute("DELETE FROM golden_sets WHERE id = ?", (str(set_id),))
            return cursor.rowcount == 1
        finally:
            connection.close()

    def count_items(self, set_id: UUID) -> int:
        connection = self._factory.connect()
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM golden_set_items WHERE golden_set_id = ?", (str(set_id),)
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def list_items(
        self,
        set_id: UUID,
        *,
        page: int,
        per_page: int,
        category: str | None,
        tag: str | None,
        enabled: bool | None,
    ) -> tuple[list[GoldenSetItemRecord], int]:
        clauses = ["golden_set_id = ?"]
        values: list[Any] = [str(set_id)]
        if category is not None:
            clauses.append("category = ?")
            values.append(category)
        if tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value = ?)")
            values.append(tag)
        if enabled is not None:
            clauses.append("enabled = ?")
            values.append(int(enabled))
        where = " AND ".join(clauses)
        connection = self._factory.connect()
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM golden_set_items WHERE {where}", values
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM golden_set_items WHERE {where} "
                "ORDER BY sort_order, case_id LIMIT ? OFFSET ?",
                (*values, per_page, (page - 1) * per_page),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_item(row) for row in rows], total

    def list_all_items(self, set_id: UUID) -> list[GoldenSetItemRecord]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM golden_set_items WHERE golden_set_id = ? "
                "ORDER BY sort_order, case_id",
                (str(set_id),),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_item(row) for row in rows]

    def get_item(self, item_id: UUID) -> GoldenSetItemRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM golden_set_items WHERE id = ?", (str(item_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map_item(row)

    def next_sort_order(self, set_id: UUID) -> int:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM golden_set_items "
                "WHERE golden_set_id = ?",
                (str(set_id),),
            ).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def save_item(self, record: GoldenSetItemRecord, previous_order: int | None = None) -> None:
        data = record.data
        assert data.sort_order is not None
        connection = self._factory.connect()
        try:
            with transaction(connection):
                if previous_order is not None and previous_order != data.sort_order:
                    connection.execute(
                        "UPDATE golden_set_items SET sort_order = 2000000000 WHERE id = ?",
                        (str(record.id),),
                    )
                    low, high = sorted((data.sort_order, previous_order))
                    connection.execute(
                        """UPDATE golden_set_items SET sort_order = sort_order + 1000000
                        WHERE golden_set_id = ? AND id != ?
                        AND sort_order >= ? AND sort_order <= ?""",
                        (str(record.golden_set_id), str(record.id), low, high),
                    )
                    delta = -999999 if data.sort_order < previous_order else -1000001
                    connection.execute(
                        """UPDATE golden_set_items SET sort_order = sort_order + ?
                        WHERE golden_set_id = ? AND id != ?
                        AND sort_order >= ? AND sort_order <= ?""",
                        (
                            delta,
                            str(record.golden_set_id),
                            str(record.id),
                            low + 1000000,
                            high + 1000000,
                        ),
                    )
                connection.execute(
                    """INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET case_id=excluded.case_id,prompt=excluded.prompt,
                    system_prompt=excluded.system_prompt,expected_answer=excluded.expected_answer,
                    evaluation_method=excluded.evaluation_method,
                    evaluator_config_json=excluded.evaluator_config_json,
                    category=excluded.category,tags_json=excluded.tags_json,weight=excluded.weight,
                    enabled=excluded.enabled,sort_order=excluded.sort_order,
                    updated_at=excluded.updated_at""",
                    (
                        str(record.id),
                        str(record.golden_set_id),
                        data.case_id,
                        data.prompt,
                        data.system_prompt,
                        data.expected_answer,
                        data.evaluation_method,
                        json.dumps(data.evaluator_config, sort_keys=True, separators=(",", ":")),
                        data.category,
                        json.dumps(data.tags),
                        str(data.weight),
                        int(data.enabled),
                        data.sort_order,
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
        finally:
            connection.close()

    def delete_items(self, set_id: UUID, item_ids: list[UUID]) -> int:
        if not item_ids:
            return 0
        connection = self._factory.connect()
        try:
            with transaction(connection):
                placeholders = ",".join("?" for _ in item_ids)
                values = [str(set_id), *(str(item_id) for item_id in item_ids)]
                rows = connection.execute(
                    f"""SELECT id FROM golden_set_items
                    WHERE golden_set_id=? AND id IN ({placeholders})""",
                    values,
                ).fetchall()
                if len(rows) != len(item_ids):
                    raise NotFoundError("A selected test case was not found.")
                connection.execute(
                    f"""DELETE FROM golden_set_items
                    WHERE golden_set_id=? AND id IN ({placeholders})""",
                    values,
                )
                remaining = connection.execute(
                    """SELECT id FROM golden_set_items WHERE golden_set_id=?
                    ORDER BY sort_order,case_id""",
                    (str(set_id),),
                ).fetchall()
                connection.executemany(
                    "UPDATE golden_set_items SET sort_order=? WHERE id=?",
                    [(index, row["id"]) for index, row in enumerate(remaining)],
                )
            return len(rows)
        finally:
            connection.close()

    def delete_item(self, item_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                row = connection.execute(
                    "SELECT golden_set_id, sort_order FROM golden_set_items WHERE id = ?",
                    (str(item_id),),
                ).fetchone()
                if row is None:
                    return False
                connection.execute("DELETE FROM golden_set_items WHERE id = ?", (str(item_id),))
                connection.execute(
                    """UPDATE golden_set_items SET sort_order = sort_order - 1
                    WHERE golden_set_id = ? AND sort_order > ?""",
                    (row["golden_set_id"], row["sort_order"]),
                )
            return True
        finally:
            connection.close()

    @staticmethod
    def _map_set(row: sqlite3.Row) -> GoldenSetRecord:
        return GoldenSetRecord(
            id=UUID(row["id"]),
            data=GoldenSetInput(
                name=row["name"],
                description=row["description"],
                version=row["version"],
                tags=json.loads(row["tags_json"]),
                active=bool(row["active"]),
            ),
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
        )

    @staticmethod
    def _map_item(row: sqlite3.Row) -> GoldenSetItemRecord:
        data = GoldenSetItemInput(
            case_id=row["case_id"],
            prompt=row["prompt"],
            system_prompt=row["system_prompt"],
            expected_answer=row["expected_answer"],
            evaluation_method=row["evaluation_method"],
            evaluator_config=json.loads(row["evaluator_config_json"]),
            category=row["category"],
            tags=json.loads(row["tags_json"]),
            weight=Decimal(row["weight"]),
            enabled=bool(row["enabled"]),
            sort_order=row["sort_order"],
        )
        return GoldenSetItemRecord(
            id=UUID(row["id"]),
            golden_set_id=UUID(row["golden_set_id"]),
            data=data,
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
        )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GoldenSetService:
    def __init__(self, repository: SQLiteGoldenSetRepository, limits: GoldenSetLimits) -> None:
        self._repository = repository
        self._limits = limits

    @staticmethod
    def _pagination(page: int, per_page: int) -> None:
        if page < 1 or per_page < 1 or per_page > 100:
            raise ValidationApiError("Invalid pagination; per_page is at most 100.")

    def list_sets(self, page: int, per_page: int) -> tuple[list[GoldenSetRecord], int]:
        self._pagination(page, per_page)
        return self._repository.list_sets(page=page, per_page=per_page)

    def get_set(self, set_id: UUID) -> GoldenSetRecord:
        result = self._repository.get_set(set_id)
        if result is None:
            raise NotFoundError("Golden set not found.")
        return result

    def create_set(self, payload: Any) -> GoldenSetRecord:
        data = self._validate(GoldenSetInput, payload)
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=data, created_at=now, updated_at=now)
        self._save_set(record)
        result = self.get_set(record.id)
        log_semantic_event(_LOG, "golden_set_created", object_id=record.id)
        return result

    def duplicate_set(self, set_id: UUID, name: str) -> GoldenSetRecord:
        source = self.get_set(set_id)
        data = self._validate(
            GoldenSetInput,
            {**source.data.model_dump(), "name": name},
        )
        source_items = self._repository.list_all_items(set_id)
        if len(source_items) > self._limits.max_items:
            raise ConflictError("Golden set bevat te veel testcases om te dupliceren.")
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=data, created_at=now, updated_at=now)
        items = [
            GoldenSetItemRecord(
                id=uuid4(),
                golden_set_id=record.id,
                data=item.data,
                created_at=now,
                updated_at=now,
            )
            for item in source_items
        ]
        try:
            self._repository.duplicate_set(set_id, record, items)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-setnaam bestaat al.") from exc
        result = self.get_set(record.id)
        log_semantic_event(
            _LOG,
            "golden_set_duplicated",
            object_id=record.id,
            source=set_id,
            case_count=len(items),
        )
        return result

    def update_set(self, set_id: UUID, payload: Any) -> GoldenSetRecord:
        existing = self.get_set(set_id)
        patch = self._validate(GoldenSetPatch, payload)
        merged = existing.data.model_dump()
        merged.update(patch.model_dump(exclude_unset=True))
        data = self._validate(GoldenSetInput, merged)
        record = GoldenSetRecord(
            id=set_id, data=data, created_at=existing.created_at, updated_at=utc_now()
        )
        self._save_set(record)
        result = self.get_set(set_id)
        log_semantic_event(_LOG, "golden_set_updated", object_id=set_id)
        return result

    def _save_set(self, record: GoldenSetRecord) -> None:
        try:
            self._repository.save_set(record)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-setnaam bestaat al.") from exc

    def delete_set(self, set_id: UUID) -> None:
        existing = self.get_set(set_id)
        try:
            if not self._repository.delete_set(set_id):
                raise NotFoundError("Golden set not found.")
        except sqlite3.IntegrityError:
            archived = existing.model_copy(
                update={
                    "data": existing.data.model_copy(update={"active": False}),
                    "updated_at": utc_now(),
                }
            )
            self._repository.save_set(archived)
        log_semantic_event(_LOG, "golden_set_deleted", object_id=set_id)

    def list_items(
        self,
        set_id: UUID,
        *,
        page: int,
        per_page: int,
        category: str | None,
        tag: str | None,
        enabled: bool | None,
    ) -> tuple[list[GoldenSetItemRecord], int]:
        self.get_set(set_id)
        self._pagination(page, per_page)
        return self._repository.list_items(
            set_id, page=page, per_page=per_page, category=category, tag=tag, enabled=enabled
        )

    def get_item(self, item_id: UUID) -> GoldenSetItemRecord:
        result = self._repository.get_item(item_id)
        if result is None:
            raise NotFoundError("Test case not found.")
        return result

    def export_set(self, set_id: UUID, format_: Literal["json", "csv"]) -> bytes:
        from evaluator.golden_set_io import export_csv, export_json

        record = self.get_set(set_id)
        items = [item.data for item in self._repository.list_all_items(set_id)]
        result = export_json(record.data, items) if format_ == "json" else export_csv(items)
        log_semantic_event(
            _LOG,
            "golden_set_exported",
            object_id=set_id,
            format=format_,
            case_count=len(items),
        )
        return result

    def import_set(
        self,
        content: bytes,
        format_: Literal["json", "csv"],
        *,
        csv_set_name: str | None,
        preview: bool,
    ) -> tuple[GoldenSetInput, list[GoldenSetItemInput], GoldenSetRecord | None]:
        from evaluator.golden_set_io import parse_import

        if len(content) > self._limits.max_import_file_bytes:
            raise ValidationApiError("Import file exceeds maximum size.")
        try:
            parsed = parse_import(content, format_, csv_set_name)
        except ValueError as exc:
            raise ValidationApiError(str(exc)) from exc
        if len(parsed.item_payloads) > self._limits.max_items:
            raise ValidationApiError("Import contains too many test cases.")
        issues: list[ErrorDetail] = []
        try:
            set_data = cast(GoldenSetInput, self._validate(GoldenSetInput, parsed.set_payload))
        except ValidationApiError as exc:
            issues.extend(
                ErrorDetail(f"set.{detail.field}", detail.message) for detail in exc.details
            )
            set_data = None
        item_data: list[GoldenSetItemInput] = []
        seen: dict[str, int] = {}
        for payload, row in zip(parsed.item_payloads, parsed.row_numbers, strict=True):
            case_id = payload.get("case_id") if isinstance(payload, dict) else None
            label = f"row {row}" + (f" ({case_id})" if isinstance(case_id, str) else "")
            try:
                item = self._validate_item(payload)
                folded = item.case_id.casefold()
                if folded in seen:
                    issues.append(ErrorDetail(label, f"case_id dupliceert rij {seen[folded]}."))
                else:
                    seen[folded] = row
                item_data.append(item.model_copy(update={"sort_order": len(item_data)}))
            except ValidationApiError as exc:
                if exc.details:
                    issues.extend(
                        ErrorDetail(f"{label}.{detail.field}", detail.message)
                        for detail in exc.details
                    )
                else:
                    issues.append(ErrorDetail(label, exc.message))
        if issues:
            raise ValidationApiError("Import contains invalid data.", details=issues)
        assert set_data is not None
        if preview:
            log_semantic_event(
                _LOG,
                "golden_set_import_previewed",
                format=format_,
                case_count=len(item_data),
                preview=True,
            )
            return set_data, item_data, None
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=set_data, created_at=now, updated_at=now)
        records = [
            GoldenSetItemRecord(
                id=uuid4(),
                golden_set_id=record.id,
                data=item,
                created_at=now,
                updated_at=now,
            )
            for item in item_data
        ]
        try:
            self._repository.import_set(record, records)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-setnaam of case_id bestaat al.") from exc
        result = self.get_set(record.id)
        log_semantic_event(
            _LOG,
            "golden_set_imported",
            object_id=record.id,
            format=format_,
            case_count=len(item_data),
            preview=False,
        )
        return set_data, item_data, result

    def create_item(self, set_id: UUID, payload: Any) -> GoldenSetItemRecord:
        self.get_set(set_id)
        if self._repository.count_items(set_id) >= self._limits.max_items:
            raise ConflictError("Maximum aantal testcases voor golden set is bereikt.")
        data = self._validate_item(payload)
        data = data.model_copy(update={"sort_order": self._repository.next_sort_order(set_id)})
        now = utc_now()
        record = GoldenSetItemRecord(
            id=uuid4(), golden_set_id=set_id, data=data, created_at=now, updated_at=now
        )
        self._save_item(record)
        result = self.get_item(record.id)
        log_semantic_event(_LOG, "golden_item_created", object_id=record.id, source=set_id)
        return result

    def update_item(self, item_id: UUID, payload: Any) -> GoldenSetItemRecord:
        existing = self.get_item(item_id)
        patch = self._validate(GoldenSetItemPatch, payload)
        merged = existing.data.model_dump()
        merged.update(patch.model_dump(exclude_unset=True))
        data = self._validate_item(merged)
        record = GoldenSetItemRecord(
            id=item_id,
            golden_set_id=existing.golden_set_id,
            data=data,
            created_at=existing.created_at,
            updated_at=utc_now(),
        )
        self._save_item(record, existing.data.sort_order)
        result = self.get_item(item_id)
        log_semantic_event(
            _LOG, "golden_item_updated", object_id=item_id, source=existing.golden_set_id
        )
        return result

    def _save_item(self, record: GoldenSetItemRecord, previous_order: int | None = None) -> None:
        try:
            self._repository.save_item(record, previous_order)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("case_id bestaat al binnen golden set.") from exc

    def delete_items(self, set_id: UUID, item_ids: list[UUID]) -> int:
        self.get_set(set_id)
        unique_ids = list(dict.fromkeys(item_ids))
        if not unique_ids:
            raise ValidationApiError("Select at least one test case.")
        if len(unique_ids) > self._limits.max_items:
            raise ValidationApiError("Too many test cases selected.")
        try:
            deleted = self._repository.delete_items(set_id, unique_ids)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Een testcase heeft historische referenties.") from exc
        log_semantic_event(_LOG, "golden_items_deleted", object_id=set_id, count=deleted)
        return deleted

    def delete_item(self, item_id: UUID) -> None:
        existing = self.get_item(item_id)
        try:
            if not self._repository.delete_item(item_id):
                raise NotFoundError("Test case not found.")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Testcase heeft historische referenties.") from exc
        log_semantic_event(
            _LOG, "golden_item_deleted", object_id=item_id, source=existing.golden_set_id
        )

    def _validate_item(self, payload: Any) -> GoldenSetItemInput:
        data = cast(GoldenSetItemInput, self._validate(GoldenSetItemInput, payload))
        if len(data.prompt) > self._limits.max_prompt_chars:
            raise ValidationApiError("Prompt exceeds maximum length.")
        if (
            data.system_prompt is not None
            and len(data.system_prompt) > self._limits.max_system_prompt_chars
        ):
            raise ValidationApiError("System prompt exceeds maximum length.")
        expected_value = (
            data.evaluator_config.get("expected_value")
            if data.evaluation_method in {"exact_match", "llm_judge"}
            else data.expected_answer
        )
        if (
            expected_value is not None
            and len(expected_value) > self._limits.max_expected_answer_chars
        ):
            raise ValidationApiError("Expected answer exceeds maximum length.")
        if data.evaluation_method == "llm_judge":
            size = len(json.dumps(data.evaluator_config, ensure_ascii=False))
            if size > self._limits.max_judge_rubric_context_chars:
                raise ValidationApiError("Judge rubric and context exceed maximum length.")
        return data

    @staticmethod
    def _validate(model: type[BaseModel], payload: Any) -> Any:
        try:
            return model.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                raise ValidationApiError.from_pydantic(exc) from exc
            raise ValidationApiError(str(exc)) from exc


def golden_set_to_dict(record: GoldenSetRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        **record.data.model_dump(mode="json"),
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }


def golden_item_to_dict(record: GoldenSetItemRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "golden_set_id": str(record.golden_set_id),
        **record.data.model_dump(mode="json"),
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }
