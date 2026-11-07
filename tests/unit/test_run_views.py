from __future__ import annotations

from decimal import Decimal

import pytest

from evaluator.api.errors import ValidationApiError
from evaluator.run_views import (
    ResultQuery,
    chart_percent,
    display_metric,
    display_money,
    status_label,
)


def test_result_query_accepts_allowlisted_filters_and_sorting() -> None:
    query = ResultQuery.parse(
        {
            "status": "passed",
            "case": "abc",
            "judge_type": "llm_judge",
            "sort": "ttft",
            "direction": "desc",
        }
    )
    assert query.status == "passed"
    assert query.case_query == "abc"
    assert query.judge_type == "llm_judge"
    assert query.sort == "ttft" and query.direction == "desc"


@pytest.mark.parametrize(
    "values",
    [
        {"status": "hacked"},
        {"sort": "score; DROP TABLE runs"},
        {"direction": "sideways"},
        {"model": "bad"},
        {"judge_type": "arbitrary_python"},
    ],
)
def test_result_query_rejects_unknown_values(values: dict[str, str]) -> None:
    with pytest.raises(ValidationApiError):
        ResultQuery.parse(values)


def test_unknown_exact_money_and_badges() -> None:
    assert display_metric(None) == "Onbekend"
    assert display_metric(Decimal("1.234"), places=2, suffix=" ms") == "1.23 ms"
    assert display_money("0.1") == "0.10000000"
    assert display_money(None) == "Onbekend"
    assert chart_percent("0.755") == 76
    assert chart_percent("2") == 100
    assert chart_percent(None) == 0
    assert status_label("completed_with_errors") == "Voltooid met fouten"
