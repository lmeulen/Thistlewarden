from decimal import Decimal

import pytest
from pydantic import ValidationError

from evaluator.judging.definitions import (
    DEFAULT_GENERAL_METRIC_WEIGHTS,
    DEFAULT_PER_METRIC_PROMPTS,
    METRIC_CATALOG,
    JudgeDefinitionInput,
    JudgeDefinitionVersion,
    JudgeMetricConfiguration,
    semantic_hash,
)

pytestmark = pytest.mark.unit


def _single() -> JudgeDefinitionInput:
    return JudgeDefinitionInput(
        name="Quality",
        mode="single_prompt",
        threshold=Decimal("0.7"),
        general_instructions="Assess every metric fairly.",
        metrics=(JudgeMetricConfiguration(metric_id="correctness", weight=Decimal("2")),),
    )


def test_metric_catalog_contains_only_recommended_ten_metrics() -> None:
    assert (
        tuple(METRIC_CATALOG)
        == tuple(DEFAULT_GENERAL_METRIC_WEIGHTS)
        == (
            "correctness",
            "relevance",
            "instruction_compliance",
            "completeness",
            "clarity",
            "conciseness",
            "internal_consistency",
            "groundedness",
            "harm_prevention",
            "privacy_protection",
        )
    )


def test_default_per_metric_prompts_cover_catalog_with_scoring_and_evidence_contract() -> None:
    assert tuple(DEFAULT_PER_METRIC_PROMPTS) == tuple(METRIC_CATALOG)
    for metric_id, prompt in DEFAULT_PER_METRIC_PROMPTS.items():
        assert len(prompt) > 1_000, metric_id
        assert "candidate answer" in prompt, metric_id
        assert "expected answer or context is absent" in prompt, metric_id
        assert "do not invent one" in prompt, metric_id
        for score in ("1.0:", "0.75:", "0.5:", "0.25:", "0.0:"):
            assert score in prompt, (metric_id, score)
        assert "concise reason citing specific evidence" in prompt, metric_id
        assert "Do not penalize the same defect" in prompt, metric_id

    assert "belongs to completeness" in DEFAULT_PER_METRIC_PROMPTS["correctness"]
    assert "belongs to correctness" in DEFAULT_PER_METRIC_PROMPTS["relevance"]
    assert "belongs to conciseness" in DEFAULT_PER_METRIC_PROMPTS["clarity"]
    assert (
        "General factual accuracy belongs to correctness"
        in DEFAULT_PER_METRIC_PROMPTS["groundedness"]
    )
    assert "belongs to harm prevention" in DEFAULT_PER_METRIC_PROMPTS["privacy_protection"]


def test_single_prompt_requires_general_instructions_and_forbids_metric_prompts() -> None:
    with pytest.raises(ValidationError, match="single_prompt requires"):
        JudgeDefinitionInput(
            name="Quality",
            mode="single_prompt",
            threshold=Decimal("0.7"),
            metrics=(JudgeMetricConfiguration(metric_id="correctness", weight=Decimal("1")),),
        )
    with pytest.raises(ValidationError, match="does not allow"):
        _single().model_copy(
            update={
                "metrics": (
                    JudgeMetricConfiguration(
                        metric_id="correctness", weight=Decimal("1"), instructions="Ignore facts"
                    ),
                )
            }
        ).__class__.model_validate(
            _single().model_dump()
            | {
                "metrics": [
                    {"metric_id": "correctness", "weight": "1", "instructions": "Rate facts."}
                ]
            }
        )


def test_per_metric_requires_prompt_for_each_enabled_metric() -> None:
    with pytest.raises(ValidationError, match="per_metric requires"):
        JudgeDefinitionInput(
            name="Quality",
            mode="per_metric",
            threshold=Decimal("0.7"),
            metrics=(JudgeMetricConfiguration(metric_id="correctness", weight=Decimal("1")),),
        )


def test_published_version_hashes_complete_semantics() -> None:
    data = _single()
    version = JudgeDefinitionVersion(
        definition_id="quality-copy",
        version="1.0",
        origin="local",
        lifecycle="published",
        data=data,
        semantic_hash=semantic_hash(data),
    )
    assert version.semantic_hash == semantic_hash(data)
    with pytest.raises(ValidationError, match="semantic_hash"):
        JudgeDefinitionVersion(
            definition_id="quality-copy",
            version="1.0",
            origin="local",
            lifecycle="published",
            data=data,
            semantic_hash="0" * 64,
        )


def test_enabled_weights_must_be_positive_and_metrics_fixed() -> None:
    with pytest.raises(ValidationError, match="positive sum"):
        _single().model_copy(
            update={
                "metrics": (JudgeMetricConfiguration(metric_id="correctness", weight=Decimal(0)),)
            }
        ).__class__.model_validate(
            _single().model_dump() | {"metrics": [{"metric_id": "correctness", "weight": "0"}]}
        )
    with pytest.raises(ValidationError, match="unknown metrics"):
        JudgeDefinitionInput(
            name="Quality",
            mode="single_prompt",
            threshold=Decimal("0.7"),
            general_instructions="Assess.",
            metrics=(JudgeMetricConfiguration(metric_id="made_up", weight=Decimal("1")),),
        )
