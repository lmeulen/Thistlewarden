"""Read-only dashboard-wide statistics."""

from __future__ import annotations

from dataclasses import dataclass

from evaluator.database import SQLiteConnectionFactory


@dataclass(frozen=True, slots=True)
class DashboardStatistics:
    """Counts displayed on dashboard cards."""

    total_runs: int = 0
    active_runs: int = 0
    finished_runs: int = 0
    active_model_profiles: int = 0
    active_endpoints: int = 0
    total_model_profiles: int = 0
    enabled_cases: int = 0
    active_golden_sets: int = 0
    total_cases: int = 0


class DashboardService:
    """Collect dashboard counts with one constant-cost query round trip."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def statistics(self) -> DashboardStatistics:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """
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
                    (SELECT COUNT(*) FROM golden_set_items) AS total_cases
                """
            ).fetchone()
            if row is None:  # pragma: no cover - aggregate SELECT always returns one row
                return DashboardStatistics()
            return DashboardStatistics(**dict(row))
        finally:
            connection.close()
