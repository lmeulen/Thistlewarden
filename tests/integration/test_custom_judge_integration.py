from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, cast

import pytest
from pydantic import BaseModel, ConfigDict, Field

from evaluator import create_app
from evaluator.judge_extensions import (
    JUDGE_EXTENSION_API_VERSION,
    JudgeCriterionDecision,
    JudgeDecision,
    JudgeEvaluationRequest,
    JudgeImplementation,
    JudgeImplementationRegistry,
    JudgeProvider,
)

pytestmark = pytest.mark.integration


class CustomConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str = Field(min_length=1)


class CustomJudge:
    implementation_id: ClassVar[str] = "test.custom"
    implementation_version: ClassVar[str] = "2.1.0"
    api_version: ClassVar[str] = JUDGE_EXTENSION_API_VERSION
    configuration_model: ClassVar[type[BaseModel]] = CustomConfig

    def evaluate(self, request: JudgeEvaluationRequest, provider: JudgeProvider) -> JudgeDecision:
        return JudgeDecision(
            score=Decimal("1"),
            reason="Custom",
            criteria=tuple(
                JudgeCriterionDecision(id=item.id, score=Decimal("1"), reason="Custom")
                for item in request.preset.rubric
            ),
        )


def register_custom(registry: JudgeImplementationRegistry) -> None:
    registry.register(cast(JudgeImplementation, CustomJudge()))


def _environment(database: Path) -> dict[str, str]:
    return {
        "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
        "DATABASE_PATH": str(database),
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "LOG_LEVEL": "WARNING",
    }


def test_profile_selection_snapshot_export_and_absent_plugin_history(tmp_path: Path) -> None:
    database = tmp_path / "extensions.db"
    environment = _environment(database)
    app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
        register_judges=register_custom,
    )
    assert app.extensions["judge_implementation_registry"].frozen
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "P", "completions_url": "https://provider.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "m", "display_name": "M"}
    )
    profile = app.extensions["judge_profile_service"].create(
        {
            "name": "Custom",
            "model_profile_id": str(model.id),
            "implementation_id": "test.custom",
            "implementation_version": "2.1.0",
            "implementation_config": {"label": "audited"},
            "is_global_default": True,
        }
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "G"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "c",
            "prompt": "question",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "answer"},
        },
    )
    run_id = app.extensions["run_queue"].create_run(
        {
            "name": "Audited",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
            "judge_profile_id": str(profile.id),
        }
    )
    snapshot = app.extensions["run_queue"].judge_snapshot(run_id)
    assert snapshot is not None
    assert snapshot["implementation_id"] == "test.custom"
    assert snapshot["implementation_version"] == "2.1.0"
    assert snapshot["implementation_config"] == {"label": "audited"}
    artifact = app.extensions["run_export_service"].create_export(run_id, "json")
    try:
        exported = json.loads(artifact.path.read_bytes())
    finally:
        artifact.cleanup()
    assert json.loads(exported["run"]["judge_snapshot_json"])["implementation_id"] == "test.custom"

    connection = app.extensions["database_factory"].connect()
    try:
        connection.execute("UPDATE runs SET status='completed' WHERE id=?", (str(run_id),))
        connection.execute("UPDATE run_jobs SET status='completed' WHERE run_id=?", (str(run_id),))
        connection.commit()
    finally:
        connection.close()
    app.extensions["shutdown_services"]()

    restarted = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    assert restarted.extensions["run_view_service"].run(run_id)["status"] == "completed"
    artifact = restarted.extensions["run_export_service"].create_export(run_id, "json")
    try:
        historical = json.loads(artifact.path.read_bytes())
    finally:
        artifact.cleanup()
    historical_snapshot = json.loads(historical["run"]["judge_snapshot_json"])
    assert historical_snapshot["implementation_id"] == "test.custom"


def test_missing_plugin_blocks_new_run(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    environment = _environment(database)
    app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
        register_judges=register_custom,
    )
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "P", "completions_url": "https://provider.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "m", "display_name": "M"}
    )
    profile = app.extensions["judge_profile_service"].create(
        {
            "name": "Custom",
            "model_profile_id": str(model.id),
            "implementation_id": "test.custom",
            "implementation_version": "2.1.0",
            "implementation_config": {"label": "x"},
            "is_global_default": True,
        }
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "G"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "c",
            "prompt": "q",
            "evaluation_method": "exact_match",
            "evaluator_config": {"expected_value": "a"},
        },
    )
    active_run_id = app.extensions["run_queue"].create_run(
        {
            "name": "Active before restart",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
            "judge_profile_id": str(profile.id),
        }
    )
    app.extensions["shutdown_services"]()
    restarted = create_app(
        env_file=None, settings_environment=environment, initialize_database=True
    )
    connection = restarted.extensions["database_factory"].connect()
    try:
        row = connection.execute(
            "SELECT status,note FROM runs WHERE id=?", (str(active_run_id),)
        ).fetchone()
        assert row["status"] == "cancelled"
        assert "judge implementation is unavailable" in row["note"]
    finally:
        connection.close()
    with pytest.raises(Exception, match="implementation is unavailable"):
        restarted.extensions["run_queue"].create_run(
            {
                "name": "Blocked",
                "golden_set_id": str(golden.id),
                "model_profile_ids": [str(model.id)],
                "judge_profile_id": str(profile.id),
            }
        )
