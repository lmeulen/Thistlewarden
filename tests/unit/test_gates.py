from decimal import Decimal

import pytest

from evaluator.gates import GateOutcome, evaluate_gate

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("name", "value", "limit", "outcome"),
    [
        ("min_weighted_score", "0.7", "0.7", GateOutcome.PASSED),
        ("min_pass_rate", "0.69", "0.7", GateOutcome.FAILED),
        ("max_error_rate", "0.1", "0.1", GateOutcome.PASSED),
        ("max_mean_latency_ms", "101", "100", GateOutcome.FAILED),
        ("max_p95_latency_ms", "100", "100", GateOutcome.PASSED),
        ("max_p95_ttft_ms", "99", "100", GateOutcome.PASSED),
        ("min_mean_tps", "10", "11", GateOutcome.FAILED),
        ("max_total_cost", "2", "2", GateOutcome.PASSED),
    ],
)
def test_inclusive_all_gate_types(name: str, value: str, limit: str, outcome: GateOutcome) -> None:
    assert evaluate_gate(name, Decimal(value), Decimal(limit)) == outcome


def test_unknown_is_indeterminate() -> None:
    assert evaluate_gate("min_mean_tps", None, Decimal("1")) == GateOutcome.INDETERMINATE
