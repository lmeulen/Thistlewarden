from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evaluator.costs import (
    CostStatus,
    PricingSnapshot,
    TokenUsage,
    calculate_cost,
    decimal_text,
)

pytestmark = pytest.mark.unit
PROFILE_ID = uuid4()


def _pricing(**changes: object) -> PricingSnapshot:
    values: dict[str, object] = {
        "input_per_million": "1",
        "output_per_million": "2",
        "cached_input_per_million": "0.5",
        "reasoning_per_million": "3",
        "fixed_per_request": "0.01",
    }
    values.update(changes)
    return PricingSnapshot.model_validate(values)


def test_cost_subsets_are_not_double_counted() -> None:
    result = calculate_cost(
        model_profile_id=PROFILE_ID,
        usage=TokenUsage(
            input_tokens=1_000_000,
            output_tokens=500_000,
            cached_input_tokens=200_000,
            reasoning_tokens=100_000,
        ),
        pricing=_pricing(),
        chargeable=True,
    )

    assert result.status is CostStatus.CALCULATED
    assert result.regular_input_tokens == 800_000
    assert result.regular_output_tokens == 400_000
    assert result.input_cost == Decimal("0.8")
    assert result.cached_input_cost == Decimal("0.1")
    assert result.output_cost == Decimal("0.8")
    assert result.reasoning_cost == Decimal("0.3")
    assert result.total_cost == Decimal("2.01")


def test_context_threshold_uses_standard_at_x_and_high_at_x_plus_one() -> None:
    pricing = _pricing(
        context_threshold=1000,
        high_context_input_per_million="10",
        high_context_output_per_million="20",
        high_context_cached_input_per_million=None,
        high_context_reasoning_per_million=None,
    )
    at_x = calculate_cost(
        model_profile_id=PROFILE_ID,
        usage=TokenUsage(
            input_tokens=1000, output_tokens=10, cached_input_tokens=0, reasoning_tokens=0
        ),
        pricing=pricing,
        chargeable=False,
    )
    above = calculate_cost(
        model_profile_id=PROFILE_ID,
        usage=TokenUsage(
            input_tokens=1001, output_tokens=10, cached_input_tokens=1, reasoning_tokens=1
        ),
        pricing=pricing,
        chargeable=False,
    )
    assert at_x.selected_tier == "standard"
    assert above.selected_tier == "high_context"
    assert above.cached_input_cost == Decimal("0.00001")
    assert above.reasoning_cost == Decimal("0.00002")


def test_missing_or_invalid_usage_is_unknown() -> None:
    missing = calculate_cost(
        model_profile_id=PROFILE_ID,
        usage=TokenUsage(input_tokens=10, output_tokens=2),
        pricing=_pricing(),
        chargeable=True,
    )
    invalid = calculate_cost(
        model_profile_id=PROFILE_ID,
        usage=TokenUsage(
            input_tokens=10, output_tokens=2, cached_input_tokens=11, reasoning_tokens=0
        ),
        pricing=_pricing(),
        chargeable=True,
    )
    assert (missing.status, missing.reason, missing.total_cost) == (
        CostStatus.UNKNOWN,
        "required_usage_missing",
        None,
    )
    assert invalid.reason == "usage_subset_exceeds_total"
    assert invalid.total_cost is None


def test_fixed_cost_only_for_chargeable_call() -> None:
    usage = TokenUsage(input_tokens=0, output_tokens=0, cached_input_tokens=0, reasoning_tokens=0)
    paid = calculate_cost(
        model_profile_id=PROFILE_ID, usage=usage, pricing=_pricing(), chargeable=True
    )
    unpaid = calculate_cost(
        model_profile_id=PROFILE_ID, usage=usage, pricing=_pricing(), chargeable=False
    )
    assert paid.total_cost == Decimal("0.01")
    assert unpaid.total_cost == Decimal("0")


def test_usage_rejects_negative_tokens_and_serialization_has_eight_places() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)
    assert decimal_text(Decimal("0.123456789123")) == "0.123456789123"
    assert decimal_text(Decimal("2")) == "2.00000000"
