"""Compatibility exports for golden-set modules.

New code should import evaluator contracts, golden-set contracts, repository, or
application service from their focused modules.
"""

# ruff: noqa: F401

from evaluator.evaluators.contracts import (
    JUDGE_PRESETS,
    ContainsConfig,
    EvaluatorConfig,
    ExactMatchConfig,
    JsonSchemaConfig,
    JudgePreset,
    LlmJudgeConfig,
    NumericConfig,
    RegexConfig,
    RubricCriterion,
    evaluator_config_examples,
    get_judge_preset,
    validate_evaluator_config,
)
from evaluator.golden_set_contracts import (
    GoldenItemBulkPreview,
    GoldenItemBulkRequest,
    GoldenSetInput,
    GoldenSetItemInput,
    GoldenSetItemPatch,
    GoldenSetItemRecord,
    GoldenSetLimits,
    GoldenSetPatch,
    GoldenSetRecord,
)
from evaluator.golden_set_repository import SQLiteGoldenSetRepository
from evaluator.golden_set_service import (
    GoldenSetService,
    golden_item_to_dict,
    golden_set_to_dict,
)

__all__ = [
    "ContainsConfig",
    "EvaluatorConfig",
    "ExactMatchConfig",
    "GoldenItemBulkPreview",
    "GoldenItemBulkRequest",
    "GoldenSetInput",
    "GoldenSetItemInput",
    "GoldenSetItemPatch",
    "GoldenSetItemRecord",
    "GoldenSetLimits",
    "GoldenSetPatch",
    "GoldenSetRecord",
    "GoldenSetService",
    "JUDGE_PRESETS",
    "JudgePreset",
    "JsonSchemaConfig",
    "LlmJudgeConfig",
    "NumericConfig",
    "RegexConfig",
    "RubricCriterion",
    "SQLiteGoldenSetRepository",
    "evaluator_config_examples",
    "get_judge_preset",
    "golden_item_to_dict",
    "golden_set_to_dict",
    "validate_evaluator_config",
]
