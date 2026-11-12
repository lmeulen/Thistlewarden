from __future__ import annotations

import base64
import os
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app
from evaluator.evaluators import EvaluationResult, EvaluatorRegistry
from evaluator.golden_sets import SQLiteGoldenSetRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "evaluators.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )


def test_registry_selects_evaluator_from_persisted_item(app) -> None:
    client = app.test_client()
    set_id = client.post("/api/v1/golden-sets", json={"name": "Evaluators"}).get_json()[
        "golden_set"
    ]["id"]
    response = client.post(
        f"/api/v1/golden-sets/{set_id}/items",
        json={
            "case_id": "contains-1",
            "prompt": "Prompt",
            "evaluation_method": "contains",
            "evaluator_config": {"expected_fragments": ["alpha", "beta"], "match_mode": "all"},
        },
    )
    assert response.status_code == 201
    item_id = UUID(response.get_json()["item"]["id"])
    repository = SQLiteGoldenSetRepository(app.extensions["database_factory"])
    item = repository.get_item(item_id)
    assert item is not None

    result = EvaluatorRegistry().evaluate(item, "alpha plus beta")
    assert result.method == "contains"
    assert result.passed is True
    assert EvaluationResult.model_validate_json(result.model_dump_json()) == result


def test_api_rejects_empty_contains_fragment(app) -> None:
    client = app.test_client()
    set_id = client.post("/api/v1/golden-sets", json={"name": "Invalid"}).get_json()["golden_set"][
        "id"
    ]
    response = client.post(
        f"/api/v1/golden-sets/{set_id}/items",
        json={
            "case_id": "bad",
            "prompt": "Prompt",
            "evaluation_method": "contains",
            "evaluator_config": {"expected_fragments": ["valid", "  "]},
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"
