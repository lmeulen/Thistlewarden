from __future__ import annotations

from decimal import Decimal
from typing import Any

from evaluator.gui import _cost_score_scatter


def _aggregate(score: str, *, cost: str = "1", name: str = "Model") -> dict[str, Any]:
    return {
        "weighted_score": Decimal(score),
        "known_total_cost": Decimal(cost),
        "model": {"display_name": name},
    }


def test_scatter_uses_zero_cost_origin_and_tenth_score_ticks() -> None:
    scatter = _cost_score_scatter(
        [
            _aggregate("0.95", cost="0.000002", name="High"),
            _aggregate("0.90", cost="0.000001", name="Low"),
        ]
    )

    assert scatter["x_min"] == Decimal(0)
    assert scatter["x_max"] == Decimal("0.0000021")
    assert scatter["y_min"] == Decimal("0.9")
    assert scatter["y_max"] == Decimal(1)
    assert scatter["x_ticks"][0] == {"value": Decimal(0), "x": Decimal(70)}
    assert scatter["x_ticks"][-1] == {"value": Decimal("0.0000021"), "x": Decimal(730)}
    assert scatter["y_ticks"] == [
        {"value": Decimal("1.0"), "y": Decimal(20)},
        {"value": Decimal("0.9"), "y": Decimal(240)},
    ]
    assert all(Decimal(70) <= point["x"] <= Decimal(730) for point in scatter["points"])
    assert all(Decimal(20) <= point["y"] <= Decimal(240) for point in scatter["points"])


def test_scatter_handles_single_point_and_omits_unknowns() -> None:
    scatter = _cost_score_scatter(
        [
            _aggregate("1", cost="0", name="Only"),
            {"weighted_score": None, "known_total_cost": Decimal(3), "model": {}},
        ]
    )

    assert scatter["x_min"] == Decimal(0)
    assert scatter["x_max"] == Decimal("0.000001")
    assert scatter["y_min"] == Decimal("0.9")
    assert scatter["y_max"] == Decimal(1)
    assert scatter["points"][0]["x"] == Decimal(70)
    assert scatter["points"][0]["y"] == Decimal(20)
    assert scatter["omitted"] == 1


def test_scatter_clamps_negative_costs_and_out_of_range_scores() -> None:
    scatter = _cost_score_scatter([_aggregate("-0.1", cost="-1"), _aggregate("1.1", cost="2")])

    assert scatter["x_min"] == Decimal(0)
    assert scatter["y_min"] == Decimal(0)
    assert scatter["y_max"] == Decimal(1)
    assert [point["y"] for point in scatter["points"]] == [Decimal(240), Decimal(20)]
