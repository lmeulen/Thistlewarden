from __future__ import annotations

import pytest

from evaluator.api.errors import ValidationApiError
from evaluator.evaluation_preview import EvaluationPreviewService

pytestmark = pytest.mark.unit


def payload(method: str = "exact_match", config: dict[str, object] | None = None):
    return {
        "case_id": "preview",
        "prompt": "Prompt is not evaluated.",
        "expected_answer": "Expected",
        "evaluation_method": method,
        "evaluator_config": config or {},
    }


@pytest.mark.parametrize(
    ("method", "config", "candidate", "passed"),
    [
        ("exact_match", {}, "Expected", True),
        ("exact_match", {}, "Different", False),
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
        payload(method, config), candidate
    )
    assert result.method == method
    assert result.passed is passed
    assert result.model_dump().keys() == {"method", "passed", "score", "reason"}
    assert candidate not in result.model_dump_json()


def test_preview_rejects_invalid_config_and_large_candidate() -> None:
    service = EvaluationPreviewService(max_candidate_chars=5)
    with pytest.raises(ValidationApiError):
        service.evaluate(payload("regex", {"pattern": "["}), "value")
    with pytest.raises(ValidationApiError, match="maximum length"):
        service.evaluate(payload(), "123456")


def test_preview_rejects_llm_judge_without_calling_provider() -> None:
    with pytest.raises(ValidationApiError, match="requires a run"):
        EvaluationPreviewService(max_candidate_chars=100).evaluate(
            payload(
                "llm_judge",
                {"rubric": [{"id": "correct", "description": "Correct"}]},
            ),
            "candidate",
        )
