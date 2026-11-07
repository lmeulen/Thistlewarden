"""Evaluator contracts and deterministic implementations."""

from evaluator.evaluators.core import EvaluationResult, Evaluator, EvaluatorRegistry, normalize_text
from evaluator.evaluators.json_schema import JsonSchemaEvaluator
from evaluator.evaluators.patterns import NumericEvaluator, RegexEvaluator
from evaluator.evaluators.text import ContainsEvaluator, ExactMatchEvaluator

__all__ = [
    "ContainsEvaluator",
    "EvaluationResult",
    "Evaluator",
    "EvaluatorRegistry",
    "ExactMatchEvaluator",
    "JsonSchemaEvaluator",
    "NumericEvaluator",
    "RegexEvaluator",
    "normalize_text",
]
