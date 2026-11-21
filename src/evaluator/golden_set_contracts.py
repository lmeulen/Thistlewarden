"""Golden-set input, record, and bulk-operation contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evaluator.evaluators.contracts import ExactMatchConfig, validate_evaluator_config


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
    user_prompts: list[str] = Field(min_length=1, max_length=100)
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

    @model_validator(mode="before")
    @classmethod
    def map_legacy_prompt(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        has_prompt = "prompt" in value
        has_user_prompts = "user_prompts" in value
        if has_prompt and has_user_prompts:
            raise ValueError("prompt and user_prompts are mutually exclusive")
        if has_prompt:
            value = {**value, "user_prompts": [value["prompt"]]}
            del value["prompt"]
        return value

    @field_validator("case_id")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("user_prompts")
    @classmethod
    def valid_user_prompts(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("user prompts must not be blank")
        return values

    @property
    def prompt(self) -> str:
        """Return first user turn for legacy single-turn consumers."""

        return self.user_prompts[0]

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        return _validate_tags(values)

    @model_validator(mode="after")
    def validate_method_requirements(self) -> GoldenSetItemInput:
        config = validate_evaluator_config(self.evaluation_method, self.evaluator_config)
        normalized_config = config.model_dump(
            mode="json",
            by_alias=True,
            exclude={"evaluation_method"},
            exclude_none=self.evaluation_method == "llm_judge",
        )
        if (
            self.evaluation_method == "json_schema"
            and "expected_value" not in self.evaluator_config
        ):
            normalized_config.pop("expected_value", None)
        if self.evaluation_method == "llm_judge" and "expected_value" not in self.evaluator_config:
            normalized_config.pop("expected_value", None)
        if self.evaluation_method == "exact_match":
            exact = cast(ExactMatchConfig, config)
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
            # Preset judge references live only in evaluator_config.
            self.expected_answer = None
            self.evaluator_config = normalized_config
        return self


class GoldenSetItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str | None = Field(default=None, min_length=1, max_length=200)
    user_prompts: list[str] | None = Field(default=None, min_length=1, max_length=100)
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

    @model_validator(mode="before")
    @classmethod
    def map_legacy_prompt(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        has_prompt = "prompt" in value
        has_user_prompts = "user_prompts" in value
        if has_prompt and has_user_prompts:
            raise ValueError("prompt and user_prompts are mutually exclusive")
        if has_prompt:
            value = {**value, "user_prompts": [value["prompt"]]}
            del value["prompt"]
        return value

    @field_validator("user_prompts")
    @classmethod
    def valid_user_prompts(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() for value in values):
            raise ValueError("user prompts must not be blank")
        return values


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


class GoldenItemBulkRequest(BaseModel):
    """Validated, explicit bulk mutation over selected test-case IDs."""

    model_config = ConfigDict(extra="forbid")
    item_ids: list[UUID] = Field(min_length=1)
    expected_updated_at: dict[UUID, datetime]
    action: Literal[
        "enable",
        "disable",
        "set_category",
        "add_tags",
        "remove_tags",
        "replace_tags",
        "set_weight",
        "move",
        "duplicate",
        "reorder",
        "delete",
    ]
    category: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)
    weight: Decimal | None = Field(default=None, gt=0)
    destination_set_id: UUID | None = None
    start_order: int | None = Field(default=None, ge=0)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        return _validate_tags(values)

    @model_validator(mode="after")
    def action_fields(self) -> GoldenItemBulkRequest:
        required: dict[str, tuple[str, Any]] = {
            "set_weight": ("weight", self.weight),
            "move": ("destination_set_id", self.destination_set_id),
            "duplicate": ("destination_set_id", self.destination_set_id),
            "reorder": ("start_order", self.start_order),
        }
        if self.action in required and required[self.action][1] is None:
            raise ValueError(f"{required[self.action][0]} is required for {self.action}")
        if self.action in {"add_tags", "remove_tags", "replace_tags"} and not self.tags:
            raise ValueError(f"tags are required for {self.action}")
        return self


class GoldenItemBulkPreview(BaseModel):
    model_config = ConfigDict(frozen=True)
    request: GoldenItemBulkRequest
    affected_count: int
    action_label: str
    destination_name: str | None = None
    changed_fields: tuple[str, ...]


class GoldenSetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_items: int = Field(gt=0)
    max_prompt_chars: int = Field(gt=0)
    max_system_prompt_chars: int = Field(gt=0)
    max_expected_answer_chars: int = Field(gt=0)
    max_judge_rubric_context_chars: int = Field(gt=0)
    max_import_file_bytes: int = Field(gt=0)
