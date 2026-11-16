"""Shared evaluator contracts, normalization, and registry."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from evaluator.evaluators.contracts import validate_evaluator_config
from evaluator.golden_set_contracts import GoldenSetItemRecord


class EvaluationResult(BaseModel):
    """Lossless result contract shared by deterministic and judge evaluators."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(min_length=1)
    score: Decimal = Field(ge=0, le=1)
    passed: bool
    reason: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evaluator(Protocol):
    method: str

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult: ...


class NormalizationOptions(Protocol):
    normalize_newlines: bool
    trim: bool
    collapse_whitespace: bool
    case_sensitive: bool


def normalize_text(
    value: str,
    *,
    normalize_newlines: bool = True,
    trim: bool = False,
    collapse_whitespace: bool = False,
    case_sensitive: bool = True,
) -> str:
    """Normalize in fixed order: newlines, trim, whitespace, then Unicode casefold."""

    if normalize_newlines:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    if trim:
        value = value.strip()
    if collapse_whitespace:
        value = re.sub(r"\s+", " ", value)
    if not case_sensitive:
        value = value.casefold()
    return value


class EvaluatorRegistry:
    def __init__(self, evaluators: list[Evaluator] | None = None) -> None:
        if evaluators is None:
            from evaluator.evaluators.json_schema import JsonSchemaEvaluator
            from evaluator.evaluators.patterns import NumericEvaluator, RegexEvaluator
            from evaluator.evaluators.text import ContainsEvaluator, ExactMatchEvaluator

            evaluators = [
                ExactMatchEvaluator(),
                ContainsEvaluator(),
                RegexEvaluator(),
                NumericEvaluator(),
                JsonSchemaEvaluator(),
            ]
        self._evaluators = {evaluator.method: evaluator for evaluator in evaluators}
        if len(self._evaluators) != len(evaluators):
            raise ValueError("evaluator methods must be unique")

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        method = item.data.evaluation_method
        # Defensive revalidation supports snapshots and detects corrupt persisted config.
        validate_evaluator_config(method, item.data.evaluator_config)
        evaluator = self._evaluators.get(method)
        if evaluator is None:
            raise ValueError(f"unsupported evaluator method: {method}")
        return evaluator.evaluate(item, candidate)
