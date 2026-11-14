"""Complete minimal trusted-local LLM judge extension for Thistlewarden.

Import ``register_judges`` from deployment's application bootstrap and pass it to
``create_app(register_judges=register_judges)``. See docs/judge-extensions.md.
"""

from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from evaluator.judge_extensions import (
    JUDGE_EXTENSION_API_VERSION,
    JudgeCriterionDecision,
    JudgeDecision,
    JudgeEvaluationRequest,
    JudgeImplementationRegistry,
    JudgeProvider,
)


class ConciseJudgeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instruction: str = Field(default="Score every criterion.", min_length=1, max_length=500)


class ConciseJudge:
    implementation_id: ClassVar[str] = "example.concise"
    implementation_version: ClassVar[str] = "1.0.0"
    api_version: ClassVar[str] = JUDGE_EXTENSION_API_VERSION
    configuration_model: ClassVar[type[BaseModel]] = ConciseJudgeConfiguration

    def evaluate(
        self, request: JudgeEvaluationRequest, provider: JudgeProvider
    ) -> JudgeDecision:
        config = ConciseJudgeConfiguration.model_validate(request.configuration)
        criteria = "\n".join(
            f"- {item.id}: {item.description}" for item in request.preset.rubric
        )
        completion = provider.complete(
            system_prompt=config.instruction,
            prompt=(
                f"Criteria:\n{criteria}\n\n"
                f"Expected: {request.expected_result!r}\n"
                f"Actual: {request.actual_result!r}\n"
                "Return no prose; this minimal example deliberately uses one fixed score."
            ),
        )
        # Real code parses ``completion.content``. Core rejects malformed IDs, bounds,
        # oversized metadata, and score/criterion inconsistency.
        decisions = tuple(
            JudgeCriterionDecision(id=item.id, score=Decimal("1"), reason="Example pass")
            for item in request.preset.rubric
        )
        return JudgeDecision(
            score=Decimal("1"),
            reason="Example implementation accepted response.",
            criteria=decisions,
            metadata={
                "response_received": bool(completion.content),
                "finish_reason": completion.finish_reason,
            },
        )


def register_judges(registry: JudgeImplementationRegistry) -> None:
    registry.register(ConciseJudge())
