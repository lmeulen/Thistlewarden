from __future__ import annotations

import pytest

from evaluator.api.errors import ValidationApiError
from evaluator.evaluation_preview import EvaluationPreviewService

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("method", "config", "candidate", "passed"),
    [
        ("exact_match", {"expected_value": "Expected"}, "Expected", True),
        ("exact_match", {"expected_value": "Expected"}, "Different", False),
        ("contains", {"expected_fragments": ["needle"]}, "a needle here", True),
        ("regex", {"pattern": r"^value-\d+$"}, "value-42", True),
        ("numeric", {"expected_value": "42", "absolute_tolerance": "0.1"}, "42.05", True),
        ("json_schema", {"schema": {"type": "object"}}, '{"valid":true}', True),
    ],
)
def test_preview_uses_production_evaluators(
    method: str, config: dict[str, object], candidate: str, passed: bool
) -> None:
    result = EvaluationPreviewService(max_candidate_chars=1000).evaluate(
        method=method,
        config=config,
        expected_answer=None,
        candidate=candidate,
    )
    assert result.method == method
    assert result.passed is passed
    assert result.model_dump().keys() == {"method", "passed", "score", "reason"}
    assert candidate not in result.model_dump_json()


def test_preview_supports_legacy_exact_match_expected_answer() -> None:
    result = EvaluationPreviewService(max_candidate_chars=100).evaluate(
        method="exact_match",
        config={},
        expected_answer="Legacy expected",
        candidate="Legacy expected",
    )
    assert result.passed is True


def test_preview_rejects_invalid_config_and_large_candidate() -> None:
    service = EvaluationPreviewService(max_candidate_chars=5)
    with pytest.raises(ValidationApiError):
        service.evaluate(
            method="regex", config={"pattern": "["}, expected_answer=None, candidate="value"
        )
    with pytest.raises(ValidationApiError, match="maximum length"):
        service.evaluate(
            method="exact_match",
            config={"expected_value": "Expected"},
            expected_answer=None,
            candidate="123456",
        )


def test_preview_limit_can_be_reconfigured_safely() -> None:
    service = EvaluationPreviewService(max_candidate_chars=100)

    service.apply_max_candidate_chars(3)

    with pytest.raises(ValidationApiError, match="maximum length"):
        service.evaluate(
            method="exact_match",
            config={"expected_value": "long"},
            expected_answer=None,
            candidate="long",
        )


def test_preview_rejects_llm_judge_without_calling_provider() -> None:
    with pytest.raises(ValidationApiError, match="requires a run"):
        EvaluationPreviewService(max_candidate_chars=100).evaluate(
            method="llm_judge",
            config={"preset_id": "general-quality", "preset_version": "1.0"},
            expected_answer=None,
            candidate="candidate",
        )
