from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import pytest

from evaluator import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    return create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "logging.db"),
            "ENCRYPTION_KEY": key,
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": False},
    )


def messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def test_get_is_debug_only_but_mutation_has_info_user_action(app, caplog) -> None:
    client = app.test_client()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.get("/api/v1/endpoints")
    assert response.status_code == 200
    assert not any("endpoint_created" in message for message in messages(caplog))

    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/v1/endpoints",
            json={"name": "Logged", "completions_url": "https://example.test/v1/chat/completions"},
        )
    assert response.status_code == 201
    event = next(message for message in messages(caplog) if "endpoint_created" in message)
    assert "actor=api" in event
    assert "route=/api/v1/endpoints" in event


def test_debug_request_logs_correlate_without_body(app, caplog) -> None:
    client = app.test_client()
    marker = "forbidden-payload-marker"
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/v1/endpoints", json={"name": marker})
    assert response.status_code == 400
    records = [record for record in caplog.records if record.name == "evaluator.api.handlers"]
    assert any(record.getMessage().startswith("request_started") for record in records)
    assert any(record.getMessage().startswith("request_completed") for record in records)
    assert all(
        getattr(record, "request_id", "-") == response.headers["X-Request-ID"] for record in records
    )
    assert marker not in "\n".join(record.getMessage() for record in records)
