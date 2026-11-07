from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from evaluator.evaluators import (
    ContainsEvaluator,
    EvaluationResult,
    ExactMatchEvaluator,
    normalize_text,
)
from evaluator.golden_sets import GoldenSetItemInput, GoldenSetItemRecord

pytestmark = pytest.mark.unit


def _record(
    method: str, config: dict[str, object], expected: str | None = "expected"
) -> GoldenSetItemRecord:
    return GoldenSetItemRecord(
        id=uuid4(),
        golden_set_id=uuid4(),
        data=GoldenSetItemInput(
            case_id="case",
            prompt="prompt",
            expected_answer=expected,
            evaluation_method=method,
            evaluator_config=config,
            sort_order=0,
        ),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_normalization_options_and_fixed_order() -> None:
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"
    assert normalize_text("  A  B \n C  ", trim=True) == "A  B \n C"
    assert normalize_text("  A  B \n C  ", collapse_whitespace=True) == " A B C "
    assert normalize_text("Straße", case_sensitive=False) == "strasse"
    # Trim happens before collapse: after trim no outer whitespace can remain.
    assert normalize_text(" \r\n A\t B \r ", trim=True, collapse_whitespace=True) == "A B"


def test_exact_match_pass_and_fail_include_normalized_metadata_not_content_in_reason() -> None:
    item = _record(
        "exact_match",
        {
            "trim": True,
            "case_sensitive": False,
            "normalize_newlines": True,
            "collapse_whitespace": True,
        },
        " STRASSE\r\nOK ",
    )
    passed = ExactMatchEvaluator().evaluate(item, "straße  OK")
    failed = ExactMatchEvaluator().evaluate(item, "secret-candidate")

    assert (passed.score, passed.passed) == (Decimal("1"), True)
    assert passed.metadata["normalized_expected"] == "strasse ok"
    assert passed.metadata["normalized_candidate"] == "strasse ok"
    assert (failed.score, failed.passed) == (Decimal("0"), False)
    assert "secret-candidate" not in failed.reason
    assert "STRASSE" not in failed.reason


def test_contains_all_any_overlap_and_unicode() -> None:
    all_item = _record(
        "contains",
        {
            "expected_fragments": ["ANA", "STRASSE"],
            "match_mode": "all",
            "case_sensitive": False,
            "collapse_whitespace": True,
        },
    )
    all_result = ContainsEvaluator().evaluate(all_item, "bananana und Straße")
    assert all_result.passed is True
    assert all_result.metadata["fragment_matches"] == [True, True]

    any_item = _record("contains", {"expected_fragments": ["missing", "nana"], "match_mode": "any"})
    any_result = ContainsEvaluator().evaluate(any_item, "bananana")
    assert any_result.passed is True
    assert any_result.metadata["matched_indexes"] == [1]


def test_contains_failure_and_newline_normalization() -> None:
    item = _record(
        "contains",
        {
            "expected_fragments": ["a\nb", "missing"],
            "match_mode": "all",
            "normalize_newlines": True,
        },
    )
    result = ContainsEvaluator().evaluate(item, "a\r\nb")
    assert result.passed is False
    assert result.score == Decimal("0")
    assert result.metadata["fragment_matches"] == [True, False]


def test_result_json_round_trip_is_lossless() -> None:
    result = EvaluationResult(
        method="exact_match",
        score=Decimal("1.0"),
        passed=True,
        reason="Normalized values match.",
        metadata={"values": ["é", True]},
    )
    restored = EvaluationResult.model_validate_json(result.model_dump_json())
    assert restored == result
