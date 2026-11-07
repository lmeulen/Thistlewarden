"""Timeout-safe regex and Decimal numeric evaluators."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast

import regex

from evaluator.evaluators.core import EvaluationResult
from evaluator.golden_sets import (
    GoldenSetItemRecord,
    NumericConfig,
    RegexConfig,
    validate_evaluator_config,
)

_REGEX_TIMEOUT_SECONDS = 0.05


class RegexEvaluator:
    method = "regex"

    def __init__(self, timeout_seconds: float = _REGEX_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("regex timeout must be positive")
        self._timeout_seconds = timeout_seconds

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        config = cast(
            RegexConfig,
            validate_evaluator_config(self.method, item.data.evaluator_config),
        )
        flags = regex.VERSION1
        if not config.case_sensitive:
            flags |= regex.IGNORECASE | regex.FULLCASE
        if config.multiline:
            flags |= regex.MULTILINE
        if config.dot_all:
            flags |= regex.DOTALL
        pattern = regex.compile(config.pattern, flags)
        try:
            match = (
                pattern.fullmatch(candidate, timeout=self._timeout_seconds)
                if config.full_match
                else pattern.search(candidate, timeout=self._timeout_seconds)
            )
        except TimeoutError:
            return EvaluationResult(
                method=self.method,
                score=Decimal(0),
                passed=False,
                reason="Regular-expression evaluation timed out.",
                metadata={"timed_out": True, "full_match": config.full_match},
            )
        passed = match is not None
        metadata: dict[str, object] = {
            "timed_out": False,
            "full_match": config.full_match,
            "matched": passed,
        }
        if match is not None:
            metadata["match_span"] = [match.start(), match.end()]
        return EvaluationResult(
            method=self.method,
            score=Decimal(1) if passed else Decimal(0),
            passed=passed,
            reason="Regular expression matched." if passed else "Regular expression did not match.",
            metadata=metadata,
        )


def _numeric_pattern(config: NumericConfig) -> str:
    decimal = r"\." if config.decimal_separator == "." else ","
    thousands = "," if config.decimal_separator == "." else r"\."
    if config.allow_thousands_separator:
        integer = rf"(?:\d{{1,3}}(?:{thousands}\d{{3}})+|\d+)"
    else:
        integer = r"\d+"
    mantissa = rf"(?:{integer}(?:{decimal}\d*)?|{decimal}\d+)"
    return rf"[+-]?{mantissa}(?:[eE][+-]?\d+)?"


def _parse_numeric(candidate: str, config: NumericConfig) -> tuple[Decimal | None, str]:
    pattern = _numeric_pattern(config)
    if config.extraction == "entire_answer":
        match = regex.fullmatch(rf"\s*({pattern})\s*", candidate, timeout=_REGEX_TIMEOUT_SECONDS)
    else:
        match = regex.search(
            rf"(?<![\w\d.,])({pattern})(?![\w\d.,])",
            candidate,
            timeout=_REGEX_TIMEOUT_SECONDS,
        )
    if match is None:
        return None, "numeric_value_not_found_or_ambiguous"
    raw = match.group(1)
    if config.allow_thousands_separator:
        separator = "," if config.decimal_separator == "." else "."
        raw = raw.replace(separator, "")
    if config.decimal_separator == ",":
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw), "ok"
    except InvalidOperation:
        return None, "numeric_value_invalid"


class NumericEvaluator:
    method = "numeric"

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        config = cast(
            NumericConfig,
            validate_evaluator_config(self.method, item.data.evaluator_config),
        )
        actual, status = _parse_numeric(candidate, config)
        if actual is None:
            return EvaluationResult(
                method=self.method,
                score=Decimal(0),
                passed=False,
                reason="Candidate does not contain one unambiguous numeric value.",
                metadata={"parse_status": status, "extraction": config.extraction},
            )
        difference = abs(actual - config.expected_value)
        checks: dict[str, bool] = {}
        if config.absolute_tolerance is not None:
            checks["absolute"] = difference <= config.absolute_tolerance
        if config.relative_tolerance_percent is not None:
            allowed = abs(config.expected_value) * config.relative_tolerance_percent / Decimal(100)
            checks["relative"] = difference <= allowed
        passed = any(checks.values()) if checks else difference == 0
        return EvaluationResult(
            method=self.method,
            score=Decimal(1) if passed else Decimal(0),
            passed=passed,
            reason="Numeric value is within tolerance."
            if passed
            else "Numeric value is outside tolerance.",
            metadata={
                "parse_status": "ok",
                "extraction": config.extraction,
                "expected_value": str(config.expected_value),
                "actual_value": str(actual),
                "absolute_difference": str(difference),
                "tolerance_checks": checks,
            },
        )
