"""Content-safe previews for deterministic test-case evaluators."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from evaluator.api.errors import ValidationApiError
from evaluator.evaluators import EvaluatorRegistry
from evaluator.golden_sets import GoldenSetItemInput, GoldenSetItemRecord
from evaluator.time import utc_now


class EvaluationPreviewResult(BaseModel):
    """Safe preview result that intentionally excludes candidate-derived metadata."""

    model_config = ConfigDict(frozen=True)

    method: str
    passed: bool
    score: str
    reason: str


class EvaluationPreviewService:
    def __init__(
        self,
        *,
        max_candidate_chars: int,
        evaluators: EvaluatorRegistry | None = None,
    ) -> None:
        if max_candidate_chars < 1:
            raise ValueError("max_candidate_chars must be positive")
        self._max_candidate_chars = max_candidate_chars
        self._evaluators = evaluators or EvaluatorRegistry()

    def evaluate(
        self,
        *,
        method: str,
        config: Any,
        expected_answer: str | None,
        candidate: str,
    ) -> EvaluationPreviewResult:
        if len(candidate) > self._max_candidate_chars:
            raise ValidationApiError("Test output exceeds maximum length.")
        try:
            data = GoldenSetItemInput.model_validate(
                {
                    "case_id": "evaluation-preview",
                    "prompt": "Evaluation preview",
                    "expected_answer": expected_answer,
                    "evaluation_method": method,
                    "evaluator_config": config,
                }
            )
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        if data.evaluation_method == "llm_judge":
            raise ValidationApiError(
                "LLM judge output testing requires a run and is unavailable in this form."
            )
        now = utc_now()
        item = GoldenSetItemRecord(
            id=UUID(int=0),
            golden_set_id=UUID(int=0),
            data=data,
            created_at=now,
            updated_at=now,
        )
        result = self._evaluators.evaluate(item, candidate)
        return EvaluationPreviewResult(
            method=result.method,
            passed=result.passed,
            score=str(result.score),
            reason=result.reason,
        )
