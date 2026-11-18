"""Versioned, immutable judge-definition contracts.

A definition controls evaluation semantics.  It deliberately excludes model,
provider, credentials, and inference settings; those belong to judge profiles.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evaluator.judging.presets import JUDGE_PRESETS

JudgeMode = Literal["single_prompt", "per_metric"]
JudgeOrigin = Literal["builtin", "local"]
JudgeLifecycle = Literal["draft", "published", "archived"]

DEFAULT_GENERAL_METRIC_WEIGHTS: dict[str, Decimal] = {
    "correctness": Decimal("3"),
    "relevance": Decimal("2"),
    "instruction_compliance": Decimal("2"),
    "completeness": Decimal("2"),
    "clarity": Decimal("1"),
    "conciseness": Decimal("1"),
    "internal_consistency": Decimal("2"),
    "groundedness": Decimal("2"),
    "harm_prevention": Decimal("2"),
    "privacy_protection": Decimal("2"),
}

DEFAULT_SINGLE_PROMPT = (
    "Assess the candidate answer against every selected metric using only the supplied "
    "prompt, expected answer, and context.\n\n"
    "Evaluate each metric independently. Base every score on specific evidence from the "
    "candidate answer. Do not reward unsupported assumptions, invented facts, verbosity, "
    "or superficial keyword overlap.\n\n"
    "Treat the expected answer as a reference for meaning and required content, not as "
    "wording that must be copied exactly. Accept alternative answers when they are correct, "
    "relevant, complete, and consistent with the supplied context.\n\n"
    "Apply score ranges consistently:\n"
    "- 1.0: fully satisfies the metric;\n"
    "- 0.75: mostly satisfies it, with minor issues;\n"
    "- 0.5: partially satisfies it, with meaningful issues;\n"
    "- 0.25: barely satisfies it;\n"
    "- 0.0: does not satisfy it.\n\n"
    "Give a concise, concrete reason for every metric score."
)


class MetricDefinition(BaseModel):
    """Code-owned metric identity and semantic description."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20_000)


class JudgeMetricConfiguration(BaseModel):
    """One fixed-catalog metric selected by a judge definition version."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    enabled: bool = True
    weight: Decimal = Field(ge=0)
    instructions: str | None = Field(default=None, min_length=1, max_length=20_000)

    @field_validator("instructions")
    @classmethod
    def strip_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("instructions must not be blank")
        return value


class JudgeDefinitionInput(BaseModel):
    """Mutable draft content; publishing creates an immutable version."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    mode: JudgeMode
    threshold: Decimal = Field(ge=0, le=1)
    general_instructions: str | None = Field(default=None, min_length=1, max_length=20_000)
    metrics: tuple[JudgeMetricConfiguration, ...] = Field(min_length=1)
    requires_expected: bool = False
    requires_context: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("description", "general_instructions")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None

    @model_validator(mode="after")
    def valid_semantics(self) -> JudgeDefinitionInput:
        metric_ids = [metric.metric_id for metric in self.metrics]
        unknown = sorted(set(metric_ids) - set(_KNOWN_METRICS))
        if unknown:
            raise ValueError(f"unknown metrics: {', '.join(unknown)}")
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metrics must not contain duplicate metric IDs")
        enabled = [metric for metric in self.metrics if metric.enabled]
        if not enabled:
            raise ValueError("at least one metric must be enabled")
        if sum((metric.weight for metric in enabled), Decimal(0)) <= 0:
            raise ValueError("enabled metric weights must have a positive sum")
        if self.mode == "single_prompt":
            if self.general_instructions is None:
                raise ValueError("single_prompt requires general_instructions")
            if any(metric.instructions is not None for metric in self.metrics):
                raise ValueError("single_prompt does not allow per-metric instructions")
        elif any(metric.instructions is None for metric in enabled):
            raise ValueError("per_metric requires instructions for every enabled metric")
        return self


class JudgeDefinitionVersion(BaseModel):
    """Published, complete, immutable semantic judge contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    definition_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=100)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$", max_length=20)
    origin: JudgeOrigin
    lifecycle: JudgeLifecycle
    data: JudgeDefinitionInput
    semantic_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def hash_matches_semantics(self) -> JudgeDefinitionVersion:
        if self.semantic_hash != semantic_hash(self.data):
            raise ValueError("semantic_hash does not match judge definition content")
        if self.lifecycle == "published" and self.origin == "builtin":
            return self
        return self


def semantic_hash(data: JudgeDefinitionInput) -> str:
    """Hash canonical semantic content; excludes identity and lifecycle."""

    payload = data.model_dump(mode="json", round_trip=True)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def metric_definition(metric_id: str) -> MetricDefinition:
    """Resolve active metrics plus legacy metrics retained for historical snapshots."""

    try:
        return _KNOWN_METRICS[metric_id]
    except KeyError as exc:
        raise ValueError(f"unknown metric: {metric_id}") from exc


def _catalog() -> dict[str, MetricDefinition]:
    values: dict[str, MetricDefinition] = {}
    for preset in JUDGE_PRESETS:
        for criterion in preset.rubric:
            existing = values.get(criterion.id)
            definition = MetricDefinition(
                id=criterion.id,
                name=criterion.id.replace("_", " ").capitalize(),
                description=criterion.description,
            )
            # Legacy presets predate a shared catalog and may use small wording
            # variations for the same metric ID. Keep first canonical wording;
            # legacy preset wording remains available through its own snapshot.
            if existing is None:
                values[criterion.id] = definition
    return values


_KNOWN_METRICS: dict[str, MetricDefinition] = _catalog()
METRIC_CATALOG: dict[str, MetricDefinition] = {
    metric_id: _KNOWN_METRICS[metric_id] for metric_id in DEFAULT_GENERAL_METRIC_WEIGHTS
}
