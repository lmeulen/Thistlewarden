from __future__ import annotations

from decimal import Decimal
from typing import Any

from evaluator.gui import (
    _cost_score_scatter,
    _model_chooser,
    _overview_model_charts,
    _quality_gate_progress,
    _sort_model_comparison,
)


def _aggregate(score: str, *, cost: str = "1", name: str = "Model") -> dict[str, Any]:
    return {
        "weighted_score": Decimal(score),
        "known_total_cost": Decimal(cost),
        "model": {"display_name": name},
    }


def test_model_chooser_uses_ranked_passing_model_and_lower_cost_tradeoff() -> None:
    recommended = _aggregate("0.9", cost="2", name="Recommended")
    recommended.update(
        {
            "rank": 1,
            "model_snapshot_id": "recommended",
            "total_cost": Decimal(2),
            "error_rate": Decimal("0.1"),
            "gates": [{"outcome": "passed"}],
        }
    )
    cheaper = _aggregate("0.8", cost="1", name="Cheaper")
    cheaper.update(
        {
            "rank": 2,
            "model_snapshot_id": "cheaper",
            "total_cost": Decimal(1),
            "error_rate": Decimal("0.2"),
            "gates": [{"outcome": "passed"}],
        }
    )
    failing = _aggregate("1", cost="3", name="Fails gate")
    failing.update(
        {
            "rank": 0,
            "model_snapshot_id": "failing",
            "total_cost": Decimal(3),
            "error_rate": Decimal(0),
            "gates": [{"outcome": "failed"}],
        }
    )

    chooser = _model_chooser([failing, cheaper, recommended], eligible=True)

    assert chooser["model"] is recommended
    assert chooser["alternative"] is cheaper
    assert chooser["cost_savings"] == Decimal(1)
    assert chooser["error_rate_delta"] == Decimal("0.1")
    assert chooser["has_configured_gates"]
    assert _model_chooser([recommended], eligible=False)["reason"] == (
        "Recommendation is available after the run completes."
    )
    assert _model_chooser([failing], eligible=True)["reason"] == (
        "No model has a score and passes every configured model gate."
    )


def test_quality_gate_progress_is_provisional_and_preserves_unknowns() -> None:
    run = {
        "terminal": False,
        "total_jobs": 4,
        "quality_gates": {
            "min_weighted_score": "0.8",
            "max_error_rate": "0.05",
            "max_total_cost": "3",
        },
    }
    aggregate = {
        "model": {"display_name": "Candidate"},
        "total": 2,
        "weighted_score": Decimal("0.76"),
        "error_rate": None,
        "known_total_cost": Decimal("1"),
        "known_cost_count": 2,
    }

    progress = _quality_gate_progress(run, [aggregate])

    assert progress is not None
    score, errors, cost = progress["entries"]
    assert score["label"] == "Weighted score"
    assert score["measured"] == Decimal("0.76")
    assert score["position"] == 66
    assert errors["measured"] is None and errors["position"] is None
    assert cost["model"] == "Entire run"
    assert cost["measured"] == Decimal(2)
    assert cost["coverage"] == "2/4 costs known"
    assert cost["extrapolated"]
    assert _quality_gate_progress({**run, "terminal": True}, [aggregate]) is None


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


def test_frontier_marks_dominated_models_and_uses_latency_and_gate_evidence() -> None:
    low_cost = _aggregate("0.8", cost="1", name="Low cost")
    low_cost.update({"latency_p95": Decimal(10), "gates": [{"outcome": "passed"}]})
    high_score = _aggregate("0.9", cost="2", name="High score")
    high_score.update({"latency_p95": Decimal(30), "gates": [{"outcome": "indeterminate"}]})
    dominated = _aggregate("0.8", cost="2", name="Dominated")
    dominated.update({"latency_p95": None, "gates": [{"outcome": "failed"}]})

    scatter = _cost_score_scatter([low_cost, high_score, dominated])
    points = {point["model"]["display_name"]: point for point in scatter["points"]}

    assert [point["model"]["display_name"] for point in scatter["frontier"]] == [
        "Low cost",
        "High score",
    ]
    assert not points["Low cost"]["dominated"]
    assert points["Dominated"]["dominated"]
    assert points["Low cost"]["gate_status"] == "passed"
    assert points["High score"]["gate_status"] == "indeterminate"
    assert points["Dominated"]["gate_status"] == "failed"
    assert points["Low cost"]["radius"] == Decimal(6)
    assert points["High score"]["radius"] == Decimal(14)
    assert points["Dominated"]["radius"] == Decimal(8)


def test_model_comparison_sorts_every_column_and_keeps_missing_values_last() -> None:
    first = {
        "rank": 2,
        "model_snapshot_id": "b",
        "model": {"display_name": "Beta"},
        "weighted_score": Decimal("0.8"),
        "latency_p95": Decimal(20),
        "ttft_p95": Decimal(10),
        "tps_mean": Decimal(30),
        "input_tokens": 7,
        "output_tokens": 2,
        "known_total_cost": Decimal("0.2"),
        "gates": [{"outcome": "passed"}],
    }
    second = {
        "rank": 1,
        "model_snapshot_id": "a",
        "model": {"display_name": "Alpha"},
        "weighted_score": Decimal("0.9"),
        "latency_p95": Decimal(30),
        "ttft_p95": Decimal(5),
        "tps_mean": Decimal(40),
        "input_tokens": 3,
        "output_tokens": 8,
        "known_total_cost": Decimal("0.1"),
        "gates": [{"outcome": "failed"}],
    }
    missing = {
        "rank": 3,
        "model_snapshot_id": "c",
        "model": {"display_name": "Missing"},
        "weighted_score": None,
        "latency_p95": None,
        "ttft_p95": None,
        "tps_mean": None,
        "input_tokens": None,
        "output_tokens": None,
        "known_total_cost": None,
        "gates": [],
    }
    items = [first, second, missing]
    expected_ascending = {
        "rank": ["a", "b", "c"],
        "model": ["a", "b", "c"],
        "score": ["b", "a", "c"],
        "latency": ["b", "a", "c"],
        "ttft": ["a", "b", "c"],
        "tps": ["b", "a", "c"],
        "tokens": ["a", "b", "c"],
        "cost": ["a", "b", "c"],
        "gates": ["a", "b", "c"],
    }
    for column, expected in expected_ascending.items():
        assert [
            item["model_snapshot_id"] for item in _sort_model_comparison(items, column, "asc")
        ] == expected
        descending = _sort_model_comparison(items, column, "desc")
        expected_descending = (
            list(reversed(expected))
            if column in {"rank", "model"}
            else [
                *reversed(expected[:-1]),
                "c",
            ]
        )
        assert [item["model_snapshot_id"] for item in descending] == expected_descending


def test_model_metric_cost_chart_uses_available_known_cost() -> None:
    charts = _overview_model_charts(
        [
            {
                "model": {"display_name": "Partially priced"},
                "weighted_score": Decimal("0.8"),
                "total_cost": None,
                "known_total_cost": Decimal("0.125"),
                "latency_mean": Decimal(10),
                "total_tokens": 5,
            }
        ]
    )

    cost = next(chart for chart in charts if chart["key"] == "cost")
    assert cost["axis_label"] == "Total known cost"
    assert cost["axis_places"] == 4
    assert cost["places"] == 6
    assert cost["maximum"] == Decimal("0.125")
    assert cost["bars"][0]["value"] == Decimal("0.125")
    assert cost["bars"][0]["height"] == Decimal(200)
    assert cost["omitted"] == 0


def test_scatter_clamps_negative_costs_and_out_of_range_scores() -> None:
    scatter = _cost_score_scatter([_aggregate("-0.1", cost="-1"), _aggregate("1.1", cost="2")])

    assert scatter["x_min"] == Decimal(0)
    assert scatter["y_min"] == Decimal(0)
    assert scatter["y_max"] == Decimal(1)
    assert [point["y"] for point in scatter["points"]] == [Decimal(240), Decimal(20)]
