from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from evaluator.gui import _cost_score_scatter


def _aggregate(score: str, *, cost: str = "1", name: str = "Model") -> dict[str, Any]:
    return {
        "weighted_score": Decimal(score),
        "known_total_cost": Decimal(cost),
        "model": {"display_name": name},
    }


@pytest.mark.parametrize(
    ("lowest_score", "expected_min"),
    [
        ("0", "0"),
        ("0.49", "0"),
        ("0.5", "0.5"),
        ("0.79", "0.5"),
        ("0.8", "0.8"),
        ("0.89", "0.8"),
        ("0.9", "0.9"),
        ("1", "0.9"),
    ],
)
def test_scatter_uses_highest_axis_minimum_containing_all_scores(
    lowest_score: str, expected_min: str
) -> None:
    scatter = _cost_score_scatter(
        [_aggregate("1", name="High"), _aggregate(lowest_score, name="Low")]
    )

    assert scatter["y_min"] == Decimal(expected_min)
    assert scatter["y_ticks"][0] == {"value": Decimal(1), "y": Decimal(20)}
    assert scatter["y_ticks"][-1] == {
        "value": Decimal(expected_min),
        "y": Decimal(240),
    }
    assert all(Decimal(20) <= point["y"] <= Decimal(240) for point in scatter["points"])


def test_scatter_scales_points_to_dynamic_axis_and_omits_unknowns() -> None:
    scatter = _cost_score_scatter(
        [
            _aggregate("0.95", cost="2", name="High"),
            _aggregate("0.9", cost="1", name="Low"),
            {"weighted_score": None, "known_total_cost": Decimal(3), "model": {}},
        ]
    )

    assert scatter["y_min"] == Decimal("0.9")
    assert scatter["points"][0]["x"] == Decimal(730)
    assert scatter["points"][0]["y"] == Decimal(130)
    assert scatter["points"][1]["x"] == Decimal(400)
    assert scatter["points"][1]["y"] == Decimal(240)
    assert scatter["omitted"] == 1


def test_scatter_clamps_out_of_range_scores_before_selecting_axis() -> None:
    scatter = _cost_score_scatter([_aggregate("-0.1"), _aggregate("1.1")])

    assert scatter["y_min"] == Decimal(0)
    assert [point["y"] for point in scatter["points"]] == [Decimal(240), Decimal(20)]
