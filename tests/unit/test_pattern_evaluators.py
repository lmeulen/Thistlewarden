from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from evaluator.evaluators import NumericEvaluator, RegexEvaluator
from evaluator.golden_sets import GoldenSetItemInput, GoldenSetItemRecord

pytestmark = pytest.mark.unit


def _item(method: str, config: dict[str, object]) -> GoldenSetItemRecord:
    now = datetime.now(UTC)
    return GoldenSetItemRecord(
        id=uuid4(),
        golden_set_id=uuid4(),
        data=GoldenSetItemInput(
            case_id="case",
            prompt="prompt",
            evaluation_method=method,
            evaluator_config=config,
            sort_order=0,
        ),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("config", "candidate", "passed"),
    [
        ({"pattern": "world"}, "hello world", True),
        ({"pattern": "world", "full_match": True}, "hello world", False),
        ({"pattern": "hello world", "full_match": True}, "hello world", True),
        ({"pattern": "STRASSE", "case_sensitive": False}, "Straße", True),
        ({"pattern": "^second$", "multiline": True}, "first\nsecond", True),
        ({"pattern": "a.b", "dot_all": True}, "a\nb", True),
    ],
)
def test_regex_modes_and_flags(config: dict[str, object], candidate: str, passed: bool) -> None:
    result = RegexEvaluator().evaluate(_item("regex", config), candidate)
    assert result.passed is passed
    assert result.score == Decimal(1 if passed else 0)


def test_catastrophic_regex_times_out_and_next_case_still_runs() -> None:
    evaluator = RegexEvaluator(timeout_seconds=0.001)
    timed_out = evaluator.evaluate(_item("regex", {"pattern": "(a+)+$"}), "a" * 20000 + "!")
    normal = evaluator.evaluate(_item("regex", {"pattern": "ok"}), "ok")
    assert timed_out.passed is False
    assert timed_out.metadata["timed_out"] is True
    assert normal.passed is True


@pytest.mark.parametrize(
    ("config", "candidate", "actual"),
    [
        ({"expected_value": "12.5"}, " 12.5 ", "12.5"),
        ({"expected_value": "12.5", "extraction": "first_number"}, "value: 12.5 EUR", "12.5"),
        ({"expected_value": "1234.5", "allow_thousands_separator": True}, "1,234.5", "1234.5"),
        (
            {
                "expected_value": "1234.5",
                "decimal_separator": ",",
                "allow_thousands_separator": True,
            },
            "1.234,5",
            "1234.5",
        ),
        ({"expected_value": "-0.001", "extraction": "first_number"}, "x=-1e-3", "-0.001"),
    ],
)
def test_numeric_parsing(config: dict[str, object], candidate: str, actual: str) -> None:
    result = NumericEvaluator().evaluate(_item("numeric", config), candidate)
    assert result.passed is True
    assert result.metadata["actual_value"] == actual


def test_numeric_entire_answer_rejects_text_multiple_values_and_bad_grouping() -> None:
    evaluator = NumericEvaluator()
    for candidate in ("value 12", "12 and 13", "1,23.4"):
        result = evaluator.evaluate(
            _item("numeric", {"expected_value": "12", "allow_thousands_separator": True}), candidate
        )
        assert result.passed is False
        assert result.metadata["parse_status"] == "numeric_value_not_found_or_ambiguous"


def test_absolute_relative_and_combined_tolerances_are_inclusive() -> None:
    evaluator = NumericEvaluator()
    absolute = evaluator.evaluate(
        _item("numeric", {"expected_value": "100", "absolute_tolerance": "0.5"}), "100.5"
    )
    relative = evaluator.evaluate(
        _item("numeric", {"expected_value": "100", "relative_tolerance_percent": "1"}), "101"
    )
    combined = evaluator.evaluate(
        _item(
            "numeric",
            {
                "expected_value": "100",
                "absolute_tolerance": "0.1",
                "relative_tolerance_percent": "1",
            },
        ),
        "100.9",
    )
    failed = evaluator.evaluate(
        _item(
            "numeric",
            {
                "expected_value": "100",
                "absolute_tolerance": "0.1",
                "relative_tolerance_percent": "0.5",
            },
        ),
        "100.6",
    )
    assert absolute.passed and relative.passed and combined.passed
    assert combined.metadata["tolerance_checks"] == {"absolute": False, "relative": True}
    assert failed.passed is False
