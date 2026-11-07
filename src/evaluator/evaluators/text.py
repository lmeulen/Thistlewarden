"""Exact-match and contains deterministic evaluators."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from evaluator.evaluators.core import EvaluationResult, normalize_text
from evaluator.golden_sets import (
    ContainsConfig,
    ExactMatchConfig,
    GoldenSetItemRecord,
    validate_evaluator_config,
)


class ExactMatchEvaluator:
    method = "exact_match"

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        config = cast(
            ExactMatchConfig,
            validate_evaluator_config(self.method, item.data.evaluator_config),
        )
        expected = item.data.expected_answer
        if expected is None:
            raise ValueError("exact_match requires expected_answer")
        options = {
            "normalize_newlines": config.normalize_newlines,
            "trim": config.trim,
            "collapse_whitespace": config.collapse_whitespace,
            "case_sensitive": config.case_sensitive,
        }
        normalized_expected = normalize_text(expected, **options)
        normalized_candidate = normalize_text(candidate, **options)
        passed = normalized_candidate == normalized_expected
        return EvaluationResult(
            method=self.method,
            score=Decimal(1) if passed else Decimal(0),
            passed=passed,
            reason="Normalized values match." if passed else "Normalized values do not match.",
            metadata={
                "normalized_expected": normalized_expected,
                "normalized_candidate": normalized_candidate,
            },
        )


class ContainsEvaluator:
    method = "contains"

    def evaluate(self, item: GoldenSetItemRecord, candidate: str) -> EvaluationResult:
        config = cast(
            ContainsConfig,
            validate_evaluator_config(self.method, item.data.evaluator_config),
        )
        options = {
            "normalize_newlines": config.normalize_newlines,
            "trim": False,
            "collapse_whitespace": config.collapse_whitespace,
            "case_sensitive": config.case_sensitive,
        }
        normalized_candidate = normalize_text(candidate, **options)
        normalized_fragments = [
            normalize_text(fragment, **options) for fragment in config.expected_fragments
        ]
        matches = [fragment in normalized_candidate for fragment in normalized_fragments]
        passed = all(matches) if config.match_mode == "all" else any(matches)
        matched_indexes = [index for index, matched in enumerate(matches) if matched]
        return EvaluationResult(
            method=self.method,
            score=Decimal(1) if passed else Decimal(0),
            passed=passed,
            reason=(
                "Required fragment condition matched."
                if passed
                else "Required fragment condition did not match."
            ),
            metadata={
                "match_mode": config.match_mode,
                "normalized_candidate": normalized_candidate,
                "normalized_fragments": normalized_fragments,
                "fragment_matches": matches,
                "matched_indexes": matched_indexes,
            },
        )
