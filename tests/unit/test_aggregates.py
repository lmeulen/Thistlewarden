from decimal import Decimal

import pytest

from evaluator.aggregates import aggregate_rows, rank_aggregates

pytestmark = pytest.mark.unit


def row(status, score, weight="1", **values):
    result = {
        "status": status,
        "score": score,
        "weight": weight,
        "target_cost": None,
        "judge_cost": None,
        "target_latency_ms": None,
        "ttft_ms": None,
        "tps": None,
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
        "result_json": "{}",
    }
    result.update(values)
    return result


def test_weighted_rates_percentiles_unknown_and_costs() -> None:
    rows = [
        row(
            "passed",
            "1",
            "2",
            target_cost="1",
            judge_cost="0",
            target_latency_ms="10",
            ttft_ms="2",
            tps="3",
            input_tokens=2,
        ),
        row(
            "failed",
            "0",
            "1",
            target_cost="2",
            judge_cost="1",
            target_latency_ms="20",
            ttft_ms="4",
            tps="6",
            input_tokens=3,
        ),
        row("error", None, target_cost="3", judge_cost=None),
    ]
    value = aggregate_rows(rows)
    assert value["weighted_score"] == Decimal("0.6666666666666666666666666667")
    assert value["pass_rate"] == Decimal("0.5") and value["error_rate"] == Decimal(1) / 3
    assert value["latency_median"] == Decimal("15") and value["latency_p95"] == Decimal("20")
    assert value["total_cost"] == Decimal("6") and value["input_tokens"] == 5
    assert value["judge_cost"] is None
    assert value["cost_per_passed"] == Decimal("6")
    assert value["known_total_cost"] == Decimal("6")
    assert value["known_cost_count"] == 3


def test_error_breakdown_keeps_guardrails_in_error_rate() -> None:
    value = aggregate_rows(
        [
            row(
                "error",
                None,
                result_json='{"outcome_category":"provider_guardrail"}',
            ),
            row("error", None, result_json='{"outcome_category":"provider_error"}'),
            row("error", None, result_json='{"outcome_category":"processing_error"}'),
            row("error", None),
        ]
    )
    assert value["errors"] == 4 and value["error_rate"] == Decimal("1")
    assert value["guardrails"] == 1 and value["guardrail_rate"] == Decimal("0.25")
    assert value["provider_errors"] == 1
    assert value["processing_errors"] == 1
    assert value["unknown_errors"] == 1


def test_known_total_cost_sums_available_target_costs() -> None:
    value = aggregate_rows([row("passed", "1", target_cost="1.25"), row("error", None)])
    assert value["total_cost"] is None
    assert value["known_total_cost"] == Decimal("1.25")
    assert value["known_cost_count"] == 1


def test_empty_and_deterministic_ranking() -> None:
    assert aggregate_rows([])["pass_rate"] is None
    values = [
        {
            "model_snapshot_id": "b",
            "weighted_score": Decimal("1"),
            "error_rate": Decimal("0"),
            "total_cost": Decimal("2"),
        },
        {
            "model_snapshot_id": "a",
            "weighted_score": Decimal("1"),
            "error_rate": Decimal("0"),
            "total_cost": Decimal("1"),
        },
    ]
    assert [x["model_snapshot_id"] for x in rank_aggregates(values)] == ["a", "b"]
