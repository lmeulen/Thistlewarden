"""Golden sets, test cases, evaluator configuration, and persistence."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from decimal import Decimal
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from evaluator.judging.presets import (
    JUDGE_PRESETS,
    JudgePreset,
    RubricCriterion,
    get_judge_preset,
)

__all__ = [
    "ContainsConfig",
    "EvaluatorConfig",
    "ExactMatchConfig",
    "JUDGE_PRESETS",
    "JudgePreset",
    "JsonSchemaConfig",
    "LlmJudgeConfig",
    "NumericConfig",
    "RegexConfig",
    "RubricCriterion",
    "evaluator_config_examples",
    "get_judge_preset",
    "validate_evaluator_config",
]

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
    case_sensitive: bool = True
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


class LlmJudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_method: Literal["llm_judge"] = "llm_judge"
    # Legacy preset references remain readable. New cases use judge_definition_*.
    preset_id: str | None = None
    preset_version: str | None = None
    judge_definition_id: str | None = None
    judge_definition_version: str | None = None
    expected_value: str | None = None
    context: str | None = None

    @model_validator(mode="after")
    def valid_judge_inputs(self) -> LlmJudgeConfig:
        legacy = self.preset_id is not None or self.preset_version is not None
        versioned = (
            self.judge_definition_id is not None or self.judge_definition_version is not None
        )
        if legacy == versioned:
            raise ValueError("select exactly one judge or legacy preset reference")
        if legacy:
            if self.preset_id is None or self.preset_version is None:
                raise ValueError("preset_id and preset_version must be supplied together")
            preset = get_judge_preset(self.preset_id, self.preset_version)
            if preset.requires_expected and self.expected_value is None:
                raise ValueError(f"{preset.id} requires expected_value")
            if preset.requires_context and self.context is None:
                raise ValueError(f"{preset.id} requires context")
        elif self.judge_definition_id is None or self.judge_definition_version is None:
            raise ValueError(
                "judge_definition_id and judge_definition_version must be supplied together"
            )
        if self.expected_value is not None and not self.expected_value.strip():
            raise ValueError("expected_value must not be blank")
        if self.context is not None and not self.context.strip():
            raise ValueError("context must not be blank")
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
        "case_sensitive": True,
        "schema_draft": "2020-12",
    },
    "llm_judge": {
        "judge_definition_id": "select-a-published-judge",
        "judge_definition_version": "1.0",
        "expected_value": None,
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
