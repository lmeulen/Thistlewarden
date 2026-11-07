from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from evaluator.models import ModelProfileInput, PricingInput, compose_inference_parameters

pytestmark = pytest.mark.unit


def test_nested_reasoning_path_builds_object() -> None:
    profile = ModelProfileInput(
        model_id="model-a",
        display_name="Reasoning high",
        reasoning_field_path="reasoning.effort",
        reasoning_value="high",
    )

    assert compose_inference_parameters(profile) == {"reasoning": {"effort": "high"}}


def test_reasoning_off_can_be_omitted_or_sent() -> None:
    omitted = ModelProfileInput(
        model_id="model-a",
        display_name="Off omitted",
        reasoning_field_path="reasoning_effort",
        reasoning_value="off",
    )
    sent = omitted.model_copy(update={"send_reasoning_off": True})

    assert "reasoning_effort" not in compose_inference_parameters(omitted)
    assert compose_inference_parameters(sent)["reasoning_effort"] == "off"


def test_default_profile_sends_no_optional_parameters() -> None:
    profile = ModelProfileInput(model_id="model-a", display_name="Minimal")
    assert compose_inference_parameters(profile) == {}


def test_maximum_output_token_binding_supports_provider_variants() -> None:
    profile = ModelProfileInput(
        model_id="model-a",
        display_name="Reasoning",
        max_tokens=123,
        max_tokens_field_path="max_completion_tokens",
        reasoning_field_path="reasoning_effort",
        reasoning_value="high",
    )
    assert compose_inference_parameters(profile) == {
        "max_completion_tokens": 123,
        "reasoning_effort": "high",
    }


def test_nested_maximum_output_token_binding() -> None:
    profile = ModelProfileInput(
        model_id="model-a",
        display_name="Nested",
        max_tokens=123,
        max_tokens_field_path="generation.max_tokens",
    )
    assert compose_inference_parameters(profile) == {"generation": {"max_tokens": 123}}


def test_unsupported_optional_parameters_are_not_null() -> None:
    profile = ModelProfileInput(
        model_id="model-a",
        display_name="Minimal",
        temperature=None,
        top_p=None,
        max_tokens=None,
    )

    assert compose_inference_parameters(profile) == {}


@pytest.mark.parametrize(
    "protected",
    ["model", "messages", "stream", "stream_options", "temperature", "top_p", "max_tokens"],
)
def test_custom_parameters_cannot_override_core_fields(protected: str) -> None:
    with pytest.raises(ValidationError, match="protected"):
        ModelProfileInput(
            model_id="model-a",
            display_name="Invalid",
            custom_parameters={protected: "override"},
        )


def test_custom_parameter_cannot_collide_with_max_tokens_binding_root() -> None:
    with pytest.raises(ValidationError, match="protected"):
        ModelProfileInput(
            model_id="model-a",
            display_name="Invalid",
            max_tokens_field_path="generation.max_tokens",
            custom_parameters={"generation": {"other": True}},
        )


def test_custom_parameter_cannot_collide_with_reasoning_root() -> None:
    with pytest.raises(ValidationError, match="protected"):
        ModelProfileInput(
            model_id="model-a",
            display_name="Invalid",
            reasoning_field_path="reasoning.effort",
            reasoning_value="high",
            custom_parameters={"reasoning": {"other": True}},
        )


def test_custom_value_must_be_finite_json() -> None:
    with pytest.raises(ValidationError, match="finite JSON"):
        ModelProfileInput(
            model_id="model-a",
            display_name="Invalid",
            custom_parameters={"scale": float("nan")},
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"context_threshold": 1000},
        {"context_threshold": 1000, "high_context_input_per_million": "2"},
        {
            "high_context_input_per_million": "2",
            "high_context_output_per_million": "3",
        },
        {"high_context_cached_input_per_million": "1"},
    ],
)
def test_partial_high_context_pricing_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PricingInput.model_validate(payload)


def test_complete_high_context_pricing_and_nonnegative_decimals() -> None:
    pricing = PricingInput(
        input_per_million="0.12345678",
        output_per_million="2.5",
        context_threshold=1000,
        high_context_input_per_million="0.2",
        high_context_output_per_million="3.0",
    )
    assert pricing.input_per_million == Decimal("0.12345678")
    with pytest.raises(ValidationError):
        PricingInput(input_per_million="-0.1")


def test_high_context_rates_apply_only_above_threshold_with_fallbacks() -> None:
    pricing = PricingInput(
        input_per_million="1",
        output_per_million="2",
        cached_input_per_million="0.5",
        reasoning_per_million="3",
        context_threshold=1000,
        high_context_input_per_million="4",
        high_context_output_per_million="5",
    )

    assert pricing.effective_rates(1000) == tuple(map(Decimal, ("1", "2", "0.5", "3")))
    assert pricing.effective_rates(1001) == tuple(map(Decimal, ("4", "5", "4", "5")))

    free_special = pricing.model_copy(
        update={
            "cached_input_per_million": Decimal("0"),
            "reasoning_per_million": Decimal("0"),
            "high_context_cached_input_per_million": Decimal("0"),
            "high_context_reasoning_per_million": Decimal("0"),
        }
    )
    assert free_special.effective_rates(1000)[2:] == (Decimal("0"), Decimal("0"))
    assert free_special.effective_rates(1001)[2:] == (Decimal("0"), Decimal("0"))
