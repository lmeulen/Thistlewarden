"""Draft 2020-12 JSON Schema evaluator."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, cast

from jsonschema import Draft202012Validator

from evaluator.evaluators.core import EvaluationResult
from evaluator.golden_sets import GoldenSetItemRecord, JsonSchemaConfig, validate_evaluator_config

_CODE_FENCE = re.compile(
    r"\A\s*```(?P<label>[A-Za-z0-9_-]*)[ \t]*\r?\n(?P<body>.*?)\r?\n```\s*\Z", re.DOTALL
)
_MAX_REASON_CHARS = 300


def _candidate_json(candidate: str, allow_fence: bool) -> tuple[Any | None, bool, str | None]:
    text = candidate.strip()
    fenced = False
    if text.startswith("```") or text.endswith("```"):
        if not allow_fence:
            return None, False, "markdown_code_fence_not_allowed"
        match = _CODE_FENCE.fullmatch(candidate)
        if match is None or match.group("label").lower() not in {"", "json"}:
            return None, False, "invalid_markdown_code_fence"
        text = match.group("body")
        fenced = True
    try:
        return json.loads(text), fenced, None
    except json.JSONDecodeError:
        return None, fenced, "invalid_json"


def _path_text(path: Any) -> str:
    values = [str(value) for value in path]
    return "$" if not values else "$[" + "][".join(values) + "]"


def _bounded_reason(prefix: str, detail: str) -> str:
    available = _MAX_REASON_CHARS - len(prefix)
    if len(detail) > available:
        detail = detail[: max(0, available - 1)] + "…"
    return prefix + detail


class JsonSchemaEvaluator:
    method = "json_schema"

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        config = cast(
            JsonSchemaConfig,
            validate_evaluator_config(self.method, item.data.evaluator_config),
        )
        parsed, fenced, parse_error = _candidate_json(candidate, config.allow_markdown_code_fence)
        if parse_error is not None:
            return EvaluationResult(
                method=self.method,
                score=Decimal(0),
                passed=False,
                reason="Candidate is not valid standalone JSON.",
                metadata={"parse_status": parse_error, "code_fence_removed": fenced},
            )

        validator = Draft202012Validator(config.schema_)
        errors = sorted(validator.iter_errors(parsed), key=lambda error: list(error.absolute_path))
        if errors:
            error = errors[0]
            path = _path_text(error.absolute_path)
            keyword = str(error.validator)[:100]
            reason = _bounded_reason(
                "JSON Schema validation failed: ", f"path {path}, rule {keyword}."
            )
            return EvaluationResult(
                method=self.method,
                score=Decimal(0),
                passed=False,
                reason=reason,
                metadata={
                    "parse_status": "ok",
                    "code_fence_removed": fenced,
                    "schema_valid": False,
                    "error_count": len(errors),
                    "first_error_path": path,
                    "parsed_value": parsed,
                },
            )

        expected_configured = "expected_value" in item.data.evaluator_config
        value_matches = not expected_configured or parsed == config.expected_value
        if not value_matches:
            return EvaluationResult(
                method=self.method,
                score=Decimal(0),
                passed=False,
                reason="JSON matches schema but differs from expected value.",
                metadata={
                    "parse_status": "ok",
                    "code_fence_removed": fenced,
                    "schema_valid": True,
                    "expected_value_matches": False,
                    "parsed_value": parsed,
                },
            )
        return EvaluationResult(
            method=self.method,
            score=Decimal(1),
            passed=True,
            reason="JSON matches configured schema and expected value constraints.",
            metadata={
                "parse_status": "ok",
                "code_fence_removed": fenced,
                "schema_valid": True,
                "expected_value_matches": True if expected_configured else None,
                "parsed_value": parsed,
            },
        )
