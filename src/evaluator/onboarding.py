"""Persistent, record-derived first-run pilot guidance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.golden_set_contracts import GoldenSetRecord
from evaluator.golden_set_service import GoldenSetService
from evaluator.time import to_utc_iso, utc_now


@dataclass(frozen=True, slots=True)
class OnboardingStep:
    key: str
    title: str
    complete: bool
    action_label: str
    action_url: str
    blocker: str


@dataclass(frozen=True, slots=True)
class OnboardingState:
    steps: tuple[OnboardingStep, ...]
    dismissed: bool
    existing_workflow: bool = False

    @property
    def complete(self) -> bool:
        return all(step.complete for step in self.steps)

    @property
    def show_checklist(self) -> bool:
        return not self.dismissed and not self.complete and not self.existing_workflow

    @property
    def completed_count(self) -> int:
        return sum(step.complete for step in self.steps)


class OnboardingService:
    """Derive readiness from durable application records and successful actions."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def state(self) -> OnboardingState:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                """SELECT
                    EXISTS(SELECT 1 FROM endpoints WHERE active=1) AS endpoint_ready,
                    EXISTS(SELECT 1 FROM onboarding_events e
                        JOIN model_profiles m ON m.id=e.object_id
                        JOIN endpoints p ON p.id=m.endpoint_id
                        WHERE e.event_type='model_test_succeeded'
                          AND m.active=1 AND p.active=1) AS model_tested,
                    EXISTS(SELECT 1 FROM golden_set_items i
                        JOIN golden_sets g ON g.id=i.golden_set_id
                        WHERE i.enabled=1 AND g.active=1) AS cases_ready,
                    EXISTS(SELECT 1 FROM onboarding_events e
                        JOIN golden_sets g ON g.id=e.object_id
                        WHERE e.event_type='evaluator_preview_succeeded' AND g.active=1)
                        AS evaluator_previewed,
                    EXISTS(SELECT 1 FROM runs r
                        WHERE (SELECT COUNT(*) FROM run_model_snapshots m
                               WHERE m.run_id=r.id)=1) AS pilot_started,
                    EXISTS(SELECT 1 FROM onboarding_events e
                        JOIN run_results rr ON rr.id=e.object_id
                        JOIN runs r ON r.id=rr.run_id
                        WHERE e.event_type='result_inspected'
                          AND r.status IN ('completed','completed_with_errors')
                    ) AS result_inspected,
                    EXISTS(SELECT 1 FROM runs r JOIN run_results rr ON rr.run_id=r.id
                        WHERE r.status IN ('completed','completed_with_errors')
                    ) AS workflow_completed,
                    EXISTS(SELECT 1 FROM onboarding_events) AS onboarding_started,
                    COALESCE((SELECT dismissed FROM onboarding_preferences WHERE id=1),0)
                        AS dismissed
                """
            ).fetchone()
            if row is None:  # pragma: no cover - aggregate SELECT always returns one row
                raise RuntimeError("Could not derive onboarding state.")
            historical = bool(row["workflow_completed"])
            endpoint = bool(row["endpoint_ready"]) or historical
            tested = bool(row["model_tested"]) or historical
            cases = bool(row["cases_ready"]) or historical
            previewed = bool(row["evaluator_previewed"]) or historical
            started = bool(row["pilot_started"]) or historical
            result = bool(row["result_inspected"])
            steps = (
                OnboardingStep(
                    "endpoint",
                    "Add a provider endpoint",
                    endpoint,
                    "Add endpoint",
                    "/endpoints/new",
                    "Add an active endpoint before configuring model access.",
                ),
                OnboardingStep(
                    "model",
                    "Test endpoint connectivity and a model profile",
                    tested,
                    "View endpoints and models",
                    "/endpoints",
                    "Add an active endpoint and model profile, then run Test model successfully."
                    if endpoint
                    else "Complete step 1 first: an active endpoint is required.",
                ),
                OnboardingStep(
                    "golden_set",
                    "Create or import a small golden set",
                    cases,
                    "Create golden set",
                    "/golden-sets/new",
                    "Create an active golden set with at least one enabled test case.",
                ),
                OnboardingStep(
                    "evaluator",
                    "Preview one deterministic evaluator",
                    previewed,
                    "View golden sets",
                    "/golden-sets",
                    "Add a deterministic test case, then use Test evaluator successfully."
                    if cases
                    else "Complete step 3 first: an enabled test case is required.",
                ),
                OnboardingStep(
                    "run",
                    "Start a one-model pilot run",
                    started,
                    "Create pilot run",
                    "/runs/new",
                    "Select one tested model and a ready golden set. Review normal cost "
                    "and sensitive-data warnings before starting."
                    if tested and cases and previewed
                    else "Complete model testing, test material, and evaluator preview first.",
                ),
                OnboardingStep(
                    "result",
                    "Inspect the first completed result",
                    result,
                    "View runs",
                    "/runs",
                    "Wait for pilot run to finish, then open its result details."
                    if started
                    else "Start a one-model pilot run first.",
                ),
            )
            return OnboardingState(
                steps=steps,
                dismissed=bool(row["dismissed"]),
                existing_workflow=historical and not bool(row["onboarding_started"]),
            )
        finally:
            connection.close()

    def record_success(
        self,
        event_type: Literal[
            "model_test_succeeded", "evaluator_preview_succeeded", "result_inspected"
        ],
        object_id: UUID,
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO onboarding_events(event_type,object_id,created_at)
                    VALUES(?,?,?) ON CONFLICT(event_type,object_id)
                    DO UPDATE SET created_at=excluded.created_at""",
                    (event_type, str(object_id), to_utc_iso(utc_now())),
                )
        finally:
            connection.close()

    def set_dismissed(self, dismissed: bool) -> None:
        now = to_utc_iso(utc_now())
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO onboarding_preferences(id,dismissed,updated_at) VALUES(1,?,?)
                    ON CONFLICT(id) DO UPDATE SET dismissed=excluded.dismissed,
                        updated_at=excluded.updated_at""",
                    (int(dismissed), now),
                )
        finally:
            connection.close()

    @staticmethod
    def create_starter_set(service: GoldenSetService) -> GoldenSetRecord:
        """Create explicit, editable synthetic content through atomic import."""
        payload = {
            "name": "Synthetic pilot examples",
            "description": (
                "Synthetic, deterministic starter content for learning the workflow. "
                "Edit or remove it at any time; it does not establish model quality."
            ),
            "version": "1.0",
            "tags": ["synthetic", "starter"],
            "items": [
                {
                    "case_id": "contains-blue",
                    "prompt": "Reply with a short sentence containing the word blue.",
                    "evaluation_method": "contains",
                    "evaluator_config": {"expected_fragments": ["blue"], "case_sensitive": False},
                    "category": "synthetic",
                    "tags": ["synthetic"],
                    "weight": "1",
                    "enabled": True,
                    "sort_order": 0,
                },
                {
                    "case_id": "exact-four",
                    "prompt": "What is two plus two? Reply with only the number.",
                    "evaluation_method": "exact_match",
                    "evaluator_config": {"expected_value": "4", "trim": True},
                    "category": "synthetic",
                    "tags": ["synthetic"],
                    "weight": "1",
                    "enabled": True,
                    "sort_order": 1,
                },
                {
                    "case_id": "json-status",
                    "prompt": "Return JSON with a string field named status.",
                    "evaluation_method": "json_schema",
                    "evaluator_config": {
                        "schema": {
                            "$schema": "https://json-schema.org/draft/2020-12/schema",
                            "type": "object",
                            "required": ["status"],
                            "properties": {"status": {"type": "string"}},
                            "additionalProperties": False,
                        }
                    },
                    "category": "synthetic",
                    "tags": ["synthetic"],
                    "weight": "1",
                    "enabled": True,
                    "sort_order": 2,
                },
            ],
        }
        _set_data, _items, record = service.import_set(
            json.dumps(payload).encode(), "json", csv_set_name=None, preview=False
        )
        if record is None:  # pragma: no cover - preview is explicitly false
            raise RuntimeError("Synthetic starter golden set was not created.")
        return record
