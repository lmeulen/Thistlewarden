from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from evaluator.golden_sets import (
    GoldenSetItemInput,
    evaluator_config_examples,
    validate_evaluator_config,
)

pytestmark = pytest.mark.unit


def _item(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "case_id": "case-1",
        "prompt": "Vraag",
        "expected_answer": "Antwoord",
        "evaluation_method": "exact_match",
        "evaluator_config": {},
    }
    value.update(changes)
    return value


def test_exact_match_requires_config_expected_value_or_legacy_value_and_positive_weight() -> None:
    with pytest.raises(ValidationError, match="expected_value"):
        GoldenSetItemInput.model_validate(_item(expected_answer=None))
    with pytest.raises(ValidationError):
        GoldenSetItemInput.model_validate(_item(weight="0"))
    legacy = GoldenSetItemInput.model_validate(_item())
    assert legacy.evaluator_config["expected_value"] == "Antwoord"
    assert legacy.expected_answer is None
    configured = GoldenSetItemInput.model_validate(
        _item(expected_answer=None, evaluator_config={"expected_value": "Configured answer"})
    )
    assert configured.evaluator_config["expected_value"] == "Configured answer"
    assert configured.weight == Decimal("1.0")


def test_prompt_is_required_and_only_one_optional_system_prompt_exists() -> None:
    with pytest.raises(ValidationError):
        GoldenSetItemInput.model_validate(_item(prompt=""))
    with pytest.raises(ValidationError):
        GoldenSetItemInput.model_validate(_item(messages=[]))
    assert GoldenSetItemInput.model_validate(_item(system_prompt=None)).system_prompt is None


@pytest.mark.parametrize(
    ("method", "config"),
    [
        ("exact_match", {"expected_value": "expected text"}),
        ("contains", {"expected_fragments": ["a"], "match_mode": "any"}),
        ("regex", {"pattern": "^ok$"}),
        ("numeric", {"expected_value": "1.25"}),
        ("json_schema", {"schema": {"type": "object"}}),
        ("llm_judge", {"threshold": "0.7"}),
    ],
)
def test_each_evaluator_method_selects_its_schema(method: str, config: dict[str, object]) -> None:
    result = validate_evaluator_config(method, config)
    assert result.evaluation_method == method


def test_evaluator_config_examples_are_complete_valid_and_independent() -> None:
    expected_methods = {
        "exact_match",
        "contains",
        "regex",
        "numeric",
        "json_schema",
        "llm_judge",
    }
    examples = evaluator_config_examples()
    assert set(examples) == expected_methods
    for method, config in examples.items():
        assert "evaluation_method" not in config
        assert validate_evaluator_config(method, config).evaluation_method == method

    examples["contains"]["expected_fragments"][0] = "changed"
    assert evaluator_config_examples()["contains"]["expected_fragments"] == ["required text"]


def test_invalid_method_specific_configuration_fails() -> None:
    with pytest.raises(ValidationError):
        validate_evaluator_config("contains", {"expected_fragments": [""]})
    with pytest.raises(ValidationError):
        validate_evaluator_config("regex", {"pattern": "["})
    with pytest.raises(ValidationError):
        validate_evaluator_config("json_schema", {"schema": {"type": "invalid"}})
    with pytest.raises(ValidationError):
        validate_evaluator_config("numeric", {"expected_value": "not-a-number"})


def test_judge_requires_config_expected_value_or_rubric_and_migrates_legacy_value() -> None:
    with pytest.raises(ValidationError, match="rubric"):
        GoldenSetItemInput.model_validate(
            _item(evaluation_method="llm_judge", evaluator_config={}, expected_answer=None)
        )
    configured = GoldenSetItemInput.model_validate(
        _item(
            evaluation_method="llm_judge",
            expected_answer=None,
            evaluator_config={"expected_value": "Configured reference"},
        )
    )
    assert configured.evaluator_config["expected_value"] == "Configured reference"
    legacy = GoldenSetItemInput.model_validate(
        _item(
            evaluation_method="llm_judge",
            evaluator_config={},
            expected_answer="Legacy reference",
        )
    )
    assert legacy.evaluator_config["expected_value"] == "Legacy reference"
    assert legacy.expected_answer is None
    rubric_only = GoldenSetItemInput.model_validate(
        _item(
            evaluation_method="llm_judge",
            expected_answer=None,
            evaluator_config={
                "rubric": [{"id": "accuracy", "description": "Correct?", "weight": "1"}]
            },
        )
    )
    assert rubric_only.expected_answer is None
    assert "expected_value" not in rubric_only.evaluator_config
