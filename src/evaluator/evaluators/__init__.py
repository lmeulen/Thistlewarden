"""Evaluator contracts and deterministic implementations."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ContainsConfig",
    "ContainsEvaluator",
    "EvaluationResult",
    "Evaluator",
    "EvaluatorConfig",
    "EvaluatorRegistry",
    "ExactMatchConfig",
    "ExactMatchEvaluator",
    "JsonSchemaConfig",
    "JsonSchemaEvaluator",
    "LlmJudgeConfig",
    "NumericConfig",
    "NumericEvaluator",
    "RegexConfig",
    "RegexEvaluator",
    "RubricCriterion",
    "evaluator_config_examples",
    "get_judge_preset",
    "normalize_text",
    "validate_evaluator_config",
]

_CONTRACTS = {
    "ContainsConfig",
    "EvaluatorConfig",
    "ExactMatchConfig",
    "JsonSchemaConfig",
    "LlmJudgeConfig",
    "NumericConfig",
    "RegexConfig",
    "RubricCriterion",
    "evaluator_config_examples",
    "get_judge_preset",
    "validate_evaluator_config",
}
_CORE = {"EvaluationResult", "Evaluator", "EvaluatorRegistry", "normalize_text"}
_IMPLEMENTATIONS = {
    "ContainsEvaluator": ("text", "ContainsEvaluator"),
    "ExactMatchEvaluator": ("text", "ExactMatchEvaluator"),
    "JsonSchemaEvaluator": ("json_schema", "JsonSchemaEvaluator"),
    "NumericEvaluator": ("patterns", "NumericEvaluator"),
    "RegexEvaluator": ("patterns", "RegexEvaluator"),
}


def __getattr__(name: str) -> Any:
    """Load public implementations only when requested, avoiding contract cycles."""

    if name in _CONTRACTS:
        from evaluator.evaluators import contracts

        return getattr(contracts, name)
    if name in _CORE:
        from evaluator.evaluators import core

        return getattr(core, name)
    module_and_name = _IMPLEMENTATIONS.get(name)
    if module_and_name is not None:
        module_name, attribute = module_and_name
        module = __import__(f"evaluator.evaluators.{module_name}", fromlist=[attribute])
        return getattr(module, attribute)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
