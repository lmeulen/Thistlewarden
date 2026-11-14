"""Read-only dashboard-wide statistics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from evaluator.database import SQLiteConnectionFactory


@dataclass(frozen=True, slots=True)
class LatestRunStatistics:
    """Headline metrics for most recently completed evaluation run."""

    id: str
    name: str
    status: str
    gate_status: str
    completed_at: str | None
    weighted_score: Decimal | None
    pass_rate: Decimal | None
    error_rate: Decimal | None
    known_total_cost: Decimal | None
    known_cost_count: int
    total_results: int


@dataclass(frozen=True, slots=True)
class DashboardStatistics:
    """Counts and latest result displayed on dashboard cards."""

    total_runs: int = 0
    active_runs: int = 0
    finished_runs: int = 0
    active_model_profiles: int = 0
    active_endpoints: int = 0
    total_model_profiles: int = 0
    enabled_cases: int = 0
    active_golden_sets: int = 0
    total_cases: int = 0
    priced_model_profiles: int = 0
    active_judge_profiles: int = 0
    total_judge_profiles: int = 0
    llm_judge_cases: int = 0
    overall_pass_rate: Decimal | None = None
    average_weighted_score: Decimal | None = None
    latest_run: LatestRunStatistics | None = None

    @property
    def pricing_coverage_percent(self) -> int:
        if not self.active_model_profiles:
            return 0
        return round(self.priced_model_profiles * 100 / self.active_model_profiles)


class DashboardService:
    """Collect dashboard counts with one constant-cost query round trip."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def statistics(self) -> DashboardStatistics:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """
                WITH run_scores AS (
                    SELECT run_id,
                        1.0 * SUM(CAST(score AS NUMERIC) * CAST(weight AS NUMERIC))
                        / SUM(CAST(weight AS NUMERIC)) AS weighted_score
                    FROM run_results
                    WHERE status != 'error' AND score IS NOT NULL
                    GROUP BY run_id
                    HAVING SUM(CAST(weight AS NUMERIC)) > 0
                ), result_statistics AS (
                    SELECT
                        SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                    FROM run_results
                )
                SELECT
                    (SELECT COUNT(*) FROM runs) AS total_runs,
                    (SELECT COUNT(*) FROM runs
                     WHERE status IN ('pending', 'running')) AS active_runs,
                    (SELECT COUNT(*) FROM runs
                     WHERE status IN (
                         'completed', 'completed_with_errors', 'failed', 'cancelled'
                     )) AS finished_runs,
                    (SELECT COUNT(*) FROM model_profiles AS model
                     JOIN endpoints AS endpoint ON endpoint.id = model.endpoint_id
                     WHERE model.active = 1 AND endpoint.active = 1) AS active_model_profiles,
                    (SELECT COUNT(*) FROM endpoints WHERE active = 1) AS active_endpoints,
                    (SELECT COUNT(*) FROM model_profiles) AS total_model_profiles,
                    (SELECT COUNT(*) FROM golden_set_items AS item
                     JOIN golden_sets AS golden_set ON golden_set.id = item.golden_set_id
                     WHERE item.enabled = 1 AND golden_set.active = 1) AS enabled_cases,
                    (SELECT COUNT(*) FROM golden_sets WHERE active = 1) AS active_golden_sets,
                    (SELECT COUNT(*) FROM golden_set_items) AS total_cases,
                    (SELECT COUNT(*) FROM golden_set_items
                     WHERE evaluation_method = 'llm_judge') AS llm_judge_cases,
                    (SELECT COUNT(*) FROM model_profiles AS model
                     JOIN endpoints AS endpoint ON endpoint.id = model.endpoint_id
                     WHERE model.active = 1 AND endpoint.active = 1 AND (
                         CAST(model.input_per_million AS NUMERIC) > 0
                         OR CAST(model.output_per_million AS NUMERIC) > 0
                         OR CAST(COALESCE(model.cached_input_per_million, '0') AS NUMERIC) > 0
                         OR CAST(COALESCE(model.reasoning_per_million, '0') AS NUMERIC) > 0
                         OR CAST(COALESCE(model.fixed_per_request, '0') AS NUMERIC) > 0
                     )) AS priced_model_profiles,
                    (SELECT COUNT(*) FROM judge_profiles WHERE active = 1)
                        AS active_judge_profiles,
                    (SELECT COUNT(*) FROM judge_profiles) AS total_judge_profiles,
                    result_statistics.passed AS result_passed,
                    result_statistics.failed AS result_failed,
                    (SELECT AVG(weighted_score) FROM run_scores) AS average_weighted_score
                FROM result_statistics
                """
            ).fetchone()
            if row is None:  # pragma: no cover - aggregate SELECT always returns one row
                return DashboardStatistics()
            values = dict(row)
            passed = int(values.pop("result_passed") or 0)
            failed = int(values.pop("result_failed") or 0)
            average_weighted_score_value = values.pop("average_weighted_score")
            overall_pass_rate = (
                Decimal(passed) / Decimal(passed + failed) if passed + failed else None
            )
            average_weighted_score = (
                Decimal(str(average_weighted_score_value))
                if average_weighted_score_value is not None
                else None
            )
            latest = connection.execute(
                """SELECT id, name, status, gate_status, completed_at
                FROM runs WHERE status IN ('completed', 'completed_with_errors')
                ORDER BY COALESCE(completed_at, created_at) DESC,
                    created_at DESC, id DESC LIMIT 1"""
            ).fetchone()
            latest_statistics = None
            if latest is not None:
                results = connection.execute(
                    """SELECT status, score, weight, target_cost
                    FROM run_results WHERE run_id = ?""",
                    (latest["id"],),
                ).fetchall()
                scored = [
                    result
                    for result in results
                    if result["status"] != "error" and result["score"] is not None
                ]
                weight_sum = sum((Decimal(result["weight"]) for result in scored), Decimal(0))
                passed = sum(result["status"] == "passed" for result in results)
                failed = sum(result["status"] == "failed" for result in results)
                errors = sum(result["status"] == "error" for result in results)
                known_costs = [
                    Decimal(result["target_cost"])
                    for result in results
                    if result["target_cost"] is not None
                ]
                latest_statistics = LatestRunStatistics(
                    **dict(latest),
                    weighted_score=(
                        sum(
                            (
                                Decimal(result["score"]) * Decimal(result["weight"])
                                for result in scored
                            ),
                            Decimal(0),
                        )
                        / weight_sum
                        if weight_sum
                        else None
                    ),
                    pass_rate=(
                        Decimal(passed) / Decimal(passed + failed) if passed + failed else None
                    ),
                    error_rate=Decimal(errors) / Decimal(len(results)) if results else None,
                    known_total_cost=sum(known_costs, Decimal(0)) if known_costs else None,
                    known_cost_count=len(known_costs),
                    total_results=len(results),
                )
            return DashboardStatistics(
                **values,
                overall_pass_rate=overall_pass_rate,
                average_weighted_score=average_weighted_score,
                latest_run=latest_statistics,
            )
        finally:
            connection.close()
