from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from evaluator.evaluators import JsonSchemaEvaluator
from evaluator.golden_sets import GoldenSetItemInput, GoldenSetItemRecord

pytestmark = pytest.mark.unit


def _item(config: dict[str, object]) -> GoldenSetItemRecord:
    now = datetime.now(UTC)
    return GoldenSetItemRecord(
        id=uuid4(),
        golden_set_id=uuid4(),
        data=GoldenSetItemInput(
            case_id="json",
            prompt="prompt",
            evaluation_method="json_schema",
            evaluator_config=config,
            sort_order=0,
        ),
        created_at=now,
        updated_at=now,
    )


def test_nested_object_arrays_required_and_types() -> None:
    item = _item(
        {
            "schema": {
                "type": "object",
                "required": ["user", "roles"],
                "properties": {
                    "user": {
                        "type": "object",
                        "required": ["age"],
                        "properties": {"age": {"type": "integer"}},
                    },
                    "roles": {"type": "array", "items": {"type": "string"}},
                },
            }
        }
    )
    valid = JsonSchemaEvaluator().evaluate(item, '{"user":{"age":42},"roles":["admin"]}')
    invalid = JsonSchemaEvaluator().evaluate(item, '{"user":{"age":"42"},"roles":[]}')
    assert valid.passed is True and valid.score == Decimal("1")
    assert invalid.passed is False
    assert invalid.metadata["schema_valid"] is False
    assert invalid.metadata["first_error_path"] == "$[user][age]"


def test_invalid_candidate_json_fails_without_system_error() -> None:
    result = JsonSchemaEvaluator().evaluate(_item({"schema": {"type": "object"}}), "{bad")
    assert result.passed is False
    assert result.metadata["parse_status"] == "invalid_json"


@pytest.mark.parametrize(
    ("candidate", "allow", "passed"),
    [
        ('```json\n{"ok":true}\n```', True, True),
        ('```JSON\n{"ok":true}\n```', True, True),
        ('```\n{"ok":true}\n```', True, True),
        ('```json\n{"ok":true}\n```', False, False),
        ('prefix ```json\n{"ok":true}\n```', True, False),
        ('```python\n{"ok":true}\n```', True, False),
        ('```json\n{"ok":true}\n``` suffix', True, False),
    ],
)
def test_code_fence_rules(candidate: str, allow: bool, passed: bool) -> None:
    result = JsonSchemaEvaluator().evaluate(
        _item({"schema": {"type": "object"}, "allow_markdown_code_fence": allow}), candidate
    )
    assert result.passed is passed


def test_deep_equality_ignores_object_key_order_but_not_array_order() -> None:
    item = _item(
        {"schema": {"type": "object"}, "expected_value": {"a": 1, "nested": {"b": [1, 2]}}}
    )
    reordered = JsonSchemaEvaluator().evaluate(item, '{"nested":{"b":[1,2]},"a":1}')
    wrong_array = JsonSchemaEvaluator().evaluate(item, '{"a":1,"nested":{"b":[2,1]}}')
    assert reordered.passed is True
    assert wrong_array.passed is False
    assert wrong_array.metadata["expected_value_matches"] is False


def test_explicit_null_expected_value_differs_from_absent() -> None:
    explicit = JsonSchemaEvaluator().evaluate(_item({"schema": {}, "expected_value": None}), "null")
    mismatch = JsonSchemaEvaluator().evaluate(_item({"schema": {}, "expected_value": None}), "{}")
    absent = JsonSchemaEvaluator().evaluate(_item({"schema": {}}), "{}")
    assert explicit.passed is True
    assert mismatch.passed is False
    assert absent.passed is True
    assert absent.metadata["expected_value_matches"] is None


def test_schema_error_reason_is_bounded() -> None:
    long_key = "field" * 100
    result = JsonSchemaEvaluator().evaluate(
        _item({"schema": {"type": "object", "properties": {long_key: {"type": "integer"}}}}),
        '{"' + long_key + '":"wrong"}',
    )
    assert result.passed is False
    assert len(result.reason) <= 300
    assert result.reason.endswith("…")
    assert "wrong" not in result.reason
