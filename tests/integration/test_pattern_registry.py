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
            "DATABASE_PATH": str(tmp_path / "patterns.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )


def test_persisted_regex_and_numeric_configs_prepare_and_round_trip(app) -> None:
    client = app.test_client()
    set_id = client.post("/api/v1/golden-sets", json={"name": "Patterns"}).get_json()["golden_set"][
        "id"
    ]
    payloads = [
        {
            "case_id": "regex",
            "prompt": "p",
            "evaluation_method": "regex",
            "evaluator_config": {"pattern": "^OK$", "case_sensitive": False},
        },
        {
            "case_id": "numeric",
            "prompt": "p",
            "evaluation_method": "numeric",
            "evaluator_config": {"expected_value": "10", "absolute_tolerance": "0.1"},
        },
    ]
    ids = []
    for payload in payloads:
        response = client.post(f"/api/v1/golden-sets/{set_id}/items", json=payload)
        assert response.status_code == 201
        ids.append(UUID(response.get_json()["item"]["id"]))
    repository = SQLiteGoldenSetRepository(app.extensions["database_factory"])
    candidates = ["ok", "10.1"]
    for item_id, candidate in zip(ids, candidates, strict=True):
        item = repository.get_item(item_id)
        assert item is not None
        result = EvaluatorRegistry().evaluate(item, candidate)
        assert result.passed is True
        assert EvaluationResult.model_validate_json(result.model_dump_json()) == result
