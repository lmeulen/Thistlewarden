from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, ConfigDict

from evaluator.judge_extensions import (
    JUDGE_EXTENSION_API_VERSION,
    JudgeDecision,
    JudgeDecisionError,
    JudgeEvaluationRequest,
    JudgeImplementation,
    JudgeImplementationRegistry,
    JudgeImplementationUnavailable,
    JudgeMessage,
    JudgePresetSnapshot,
    JudgeProvider,
    JudgeRegistrationError,
    canonical_messages,
    validate_decision,
)

pytestmark = pytest.mark.unit


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strict: bool = True


class FakeJudge:
    implementation_id: ClassVar[str] = "test.fake"
    implementation_version: ClassVar[str] = "1.0.0"
    api_version: ClassVar[str] = JUDGE_EXTENSION_API_VERSION
    configuration_model: ClassVar[type[BaseModel]] = Config

    def evaluate(self, request: JudgeEvaluationRequest, provider: JudgeProvider) -> JudgeDecision:
        raise NotImplementedError


def _registry() -> JudgeImplementationRegistry:
    registry = JudgeImplementationRegistry()
    registry.register(cast(JudgeImplementation, FakeJudge()))
    return registry


def _request() -> JudgeEvaluationRequest:
    return JudgeEvaluationRequest(
        messages=(JudgeMessage(role="user", content="Question"),),
        expected_result="Expected",
        actual_result="Actual",
        context="Context",
        preset=JudgePresetSnapshot(
            id="test",
            version="1.0",
            name="Test",
            description="Test preset",
            threshold=Decimal("0.7"),
            rubric=(
                {"id": "a", "description": "A", "weight": "3"},
                {"id": "b", "description": "B", "weight": "1"},
            ),
        ),
        configuration={"strict": True},
    )


def _decision(**updates: Any) -> JudgeDecision:
    payload: dict[str, Any] = {
        "score": "0.75",
        "reason": "Result",
        "criteria": (
            {"id": "a", "score": "1", "reason": "A"},
            {"id": "b", "score": "0", "reason": "B"},
        ),
        "metadata": {"safe": True},
    }
    payload.update(updates)
    return JudgeDecision.model_validate(payload)


def test_registry_rejects_collision_late_registration_and_missing_version() -> None:
    registry = _registry()
    with pytest.raises(JudgeRegistrationError, match="already registered"):
        registry.register(cast(JudgeImplementation, FakeJudge()))
    registry.freeze()
    with pytest.raises(JudgeRegistrationError, match="frozen"):
        registry.register(cast(JudgeImplementation, FakeJudge()))
    with pytest.raises(JudgeImplementationUnavailable, match="unavailable"):
        registry.get("test.fake", "2.0.0")


def test_configuration_is_strict_bounded_and_rejects_secret_fields() -> None:
    registry = _registry()
    assert registry.validate_configuration("test.fake", "1.0.0", {"strict": False}) == {
        "strict": False
    }
    with pytest.raises(JudgeRegistrationError, match="invalid"):
        registry.validate_configuration("test.fake", "1.0.0", {"unknown": True})

    class SecretConfig(BaseModel):
        model_config = ConfigDict(extra="forbid")
        api_token: str

    FakeJudge.configuration_model = SecretConfig
    with pytest.raises(JudgeRegistrationError, match="credentials"):
        registry.validate_configuration("test.fake", "1.0.0", {"api_token": "not-persisted"})


def test_canonical_messages_preserve_system_then_user_text() -> None:
    assert canonical_messages("user", "system") == (
        JudgeMessage(role="system", content="system"),
        JudgeMessage(role="user", content="user"),
    )
    assert canonical_messages("user", None) == (JudgeMessage(role="user", content="user"),)


def test_core_calculates_threshold_and_validates_decision() -> None:
    result = validate_decision(
        _decision(),
        _request(),
        implementation_id="test.fake",
        implementation_version="1.0.0",
    )
    assert result.score == Decimal("0.75") and result.passed
    assert result.metadata["implementation_metadata"] == {"safe": True}
    assert result.metadata["implementation_id"] == "test.fake"

    with pytest.raises(JudgeDecisionError, match="criteria"):
        validate_decision(
            _decision(criteria=tuple(reversed(_decision().criteria))),
            _request(),
            implementation_id="test.fake",
            implementation_version="1.0.0",
        )
    with pytest.raises(JudgeDecisionError, match="inconsistent"):
        validate_decision(
            _decision(score="0.1"),
            _request(),
            implementation_id="test.fake",
            implementation_version="1.0.0",
        )


def test_metadata_must_be_json_and_bounded() -> None:
    for metadata in ({"bad": object()}, {"large": "x" * (33 * 1024)}):
        decision = _decision().model_copy(update={"metadata": metadata})
        with pytest.raises(JudgeDecisionError, match="metadata"):
            validate_decision(
                decision,
                _request(),
                implementation_id="test.fake",
                implementation_version="1.0.0",
            )


def test_fake_extension_can_observe_cancellation_without_runtime_services() -> None:
    cancelled = threading.Event()

    class Provider:
        def complete(self, *, prompt: str, system_prompt: str | None = None):
            cancelled.set()
            raise RuntimeError("cancelled")

    class CancellingJudge(FakeJudge):
        def evaluate(
            self, request: JudgeEvaluationRequest, provider: JudgeProvider
        ) -> JudgeDecision:
            provider.complete(prompt="safe generated prompt")
            raise AssertionError

    with pytest.raises(RuntimeError, match="cancelled"):
        CancellingJudge().evaluate(_request(), Provider())  # type: ignore[arg-type]
    assert cancelled.is_set()
