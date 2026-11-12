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
            "DATABASE_PATH": str(tmp_path / "json-schema.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )


def test_invalid_schema_blocks_storage_and_valid_persisted_schema_evaluates(app) -> None:
    client = app.test_client()
    set_id = client.post("/api/v1/golden-sets", json={"name": "JSON"}).get_json()["golden_set"][
        "id"
    ]
    invalid = client.post(
        f"/api/v1/golden-sets/{set_id}/items",
        json={
            "case_id": "bad",
            "prompt": "p",
            "evaluation_method": "json_schema",
            "evaluator_config": {"schema": {"type": "not-a-type"}},
        },
    )
    assert invalid.status_code == 400
    valid = client.post(
        f"/api/v1/golden-sets/{set_id}/items",
        json={
            "case_id": "good",
            "prompt": "p",
            "evaluation_method": "json_schema",
            "evaluator_config": {"schema": {"type": "object", "required": ["content"]}},
        },
    )
    assert valid.status_code == 201
    repository = SQLiteGoldenSetRepository(app.extensions["database_factory"])
    item = repository.get_item(UUID(valid.get_json()["item"]["id"]))
    assert item is not None
    result = EvaluatorRegistry().evaluate(item, '{"content":"<script>alert(1)</script>"}')
    assert result.passed is True
    assert result.metadata["parsed_value"] == {"content": "<script>alert(1)</script>"}
    assert EvaluationResult.model_validate_json(result.model_dump_json()) == result
