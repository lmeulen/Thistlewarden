from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from uuid import UUID

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


def csrf_token(client, path: str) -> str:
    page = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert page.status_code == 200 and match
    return match.group(1)


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
    assert "endpoint_id=" in event


def test_api_and_gui_endpoint_mutations_emit_one_post_commit_service_event(app, caplog) -> None:
    client = app.test_client()
    with caplog.at_level(logging.INFO):
        api_response = client.post(
            "/api/v1/endpoints",
            json={
                "name": "API endpoint",
                "completions_url": "https://api.example/v1/chat/completions",
            },
        )
    assert api_response.status_code == 201
    api_events = [
        record for record in caplog.records if record.getMessage().startswith("endpoint_created")
    ]
    assert len(api_events) == 1
    assert api_events[0].name == "evaluator.endpoints"
    assert "actor=api" in api_events[0].getMessage()
    assert "endpoint_id=" in api_events[0].getMessage()

    caplog.clear()
    token = csrf_token(client, "/endpoints/nieuw")
    with caplog.at_level(logging.INFO):
        gui_response = client.post(
            "/endpoints/nieuw",
            data={
                "csrf_token": token,
                "name": "GUI endpoint",
                "completions_url": "https://gui.example/v1/chat/completions",
            },
        )
    assert gui_response.status_code == 302
    gui_events = [
        record for record in caplog.records if record.getMessage().startswith("endpoint_created")
    ]
    assert len(gui_events) == 1
    assert gui_events[0].name == "evaluator.endpoints"
    assert "actor=local_gui" in gui_events[0].getMessage()


def test_failed_mutation_emits_no_semantic_success_event(app, caplog) -> None:
    with caplog.at_level(logging.INFO):
        response = app.test_client().post(
            "/api/v1/endpoints",
            json={"name": "", "completions_url": "https://example.test/v1/chat/completions"},
        )

    assert response.status_code == 400
    assert not any(message.startswith("endpoint_created") for message in messages(caplog))


def test_judge_and_gate_events_are_post_commit_and_content_free(app, caplog) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Judge provider", "completions_url": "https://judge.example/v1/chat"},
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "judge-model", "display_name": "Judge model"},
    ).get_json()["model"]

    caplog.clear()
    with caplog.at_level(logging.INFO):
        judge_response = client.post(
            "/api/v1/judge-profiles",
            json={
                "name": "Main judge",
                "model_profile_id": model["id"],
                "prompt_template": "forbidden-template-content",
                "rubric": [{"id": "correct", "description": "forbidden-rubric-content"}],
            },
        )
    assert judge_response.status_code == 201
    judge_events = [
        record
        for record in caplog.records
        if record.getMessage().startswith("judge_profile_created")
    ]
    assert len(judge_events) == 1 and judge_events[0].name == "evaluator.judges"
    assert "actor=api" in judge_events[0].getMessage()
    assert "object_id=" in judge_events[0].getMessage()
    assert "forbidden" not in judge_events[0].getMessage()

    golden_set = client.post(
        "/api/v1/golden-sets",
        json={"name": "Gate set", "version": "1.0"},
    ).get_json()["golden_set"]
    caplog.clear()
    with caplog.at_level(logging.INFO):
        gate_response = client.put(
            f"/api/v1/golden-sets/{golden_set['id']}/quality-gates",
            json={"min_pass_rate": "0.812345", "max_error_rate": "0.123456"},
        )
    assert gate_response.status_code == 200
    gate_events = [
        record
        for record in caplog.records
        if record.getMessage().startswith("quality_gates_updated")
    ]
    assert len(gate_events) == 1 and gate_events[0].name == "evaluator.gates"
    gate_message = gate_events[0].getMessage()
    assert "actor=api" in gate_message
    assert "configured_gates=max_error_rate,min_pass_rate" in gate_message
    assert "0.812345" not in gate_message and "0.123456" not in gate_message


def test_failed_judge_and_gate_mutations_emit_no_success_event(app, caplog) -> None:
    client = app.test_client()
    with caplog.at_level(logging.INFO):
        judge_response = client.post(
            "/api/v1/judge-profiles",
            json={"name": "Invalid", "model_profile_id": "not-a-uuid"},
        )
        gate_response = client.put(
            "/api/v1/golden-sets/10000000-0000-4000-8000-000000000001/quality-gates",
            json={"min_pass_rate": "0.5"},
        )
    assert judge_response.status_code == 400
    assert gate_response.status_code == 404
    output = "\n".join(messages(caplog))
    assert "judge_profile_created" not in output
    assert "quality_gates_updated" not in output


def test_golden_events_are_post_commit_counted_and_content_free(app, caplog) -> None:
    client = app.test_client()
    marker = "forbidden-golden-content"
    with caplog.at_level(logging.INFO):
        created = client.post(
            "/api/v1/golden-sets",
            json={"name": marker, "description": marker, "version": "1.0"},
        )
    assert created.status_code == 201
    set_id = created.get_json()["golden_set"]["id"]
    events = [r for r in caplog.records if r.getMessage().startswith("golden_set_created")]
    assert len(events) == 1 and events[0].name == "evaluator.golden_sets"
    assert "actor=api" in events[0].getMessage() and f"object_id={set_id}" in events[0].getMessage()
    assert marker not in events[0].getMessage()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        item = client.post(
            f"/api/v1/golden-sets/{set_id}/items",
            json={
                "case_id": "safe-id",
                "prompt": marker,
                "expected_answer": marker,
                "evaluation_method": "exact_match",
                "evaluator_config": {},
            },
        )
    assert item.status_code == 201
    item_events = [r for r in caplog.records if r.getMessage().startswith("golden_item_created")]
    assert len(item_events) == 1 and item_events[0].name == "evaluator.golden_sets"
    assert marker not in item_events[0].getMessage()


def test_configuration_preview_and_export_events_use_safe_counts(app, caplog) -> None:
    client = app.test_client()
    marker = "forbidden-configuration-content"
    client.post(
        "/api/v1/endpoints",
        json={"name": marker, "completions_url": "https://config.example/v1/chat"},
    )
    caplog.clear()
    with caplog.at_level(logging.INFO):
        exported = client.get("/api/v1/configuration/export")
    assert exported.status_code == 200
    export_events = [
        r for r in caplog.records if r.getMessage().startswith("configuration_exported")
    ]
    assert len(export_events) == 1 and export_events[0].name == "evaluator.config_io"
    assert "object_count=1" in export_events[0].getMessage()
    assert marker not in export_events[0].getMessage()

    caplog.clear()
    with caplog.at_level(logging.INFO):
        previewed = client.post(
            "/api/v1/configuration/import?mode=merge&preview=true", json=exported.get_json()
        )
    assert previewed.status_code == 200
    preview_events = [
        r for r in caplog.records if r.getMessage().startswith("configuration_import_previewed")
    ]
    assert len(preview_events) == 1 and preview_events[0].name == "evaluator.config_io"
    message = preview_events[0].getMessage()
    assert "actor=api" in message and "preview=True" in message and "object_count=1" in message
    assert marker not in message


def test_run_create_and_effective_cancel_events_are_counted(app, caplog) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Run endpoint", "completions_url": "https://run.example/v1/chat"},
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "run-model", "display_name": "Run model"},
    ).get_json()["model"]
    golden_set = client.post(
        "/api/v1/golden-sets", json={"name": "Run set", "version": "1.0"}
    ).get_json()["golden_set"]
    client.post(
        f"/api/v1/golden-sets/{golden_set['id']}/items",
        json={
            "case_id": "one",
            "prompt": "forbidden-run-prompt",
            "expected_answer": "safe expected value",
            "evaluation_method": "exact_match",
            "evaluator_config": {},
        },
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        created = client.post(
            "/api/v1/runs",
            json={
                "name": "forbidden-run-name",
                "golden_set_id": golden_set["id"],
                "model_profile_ids": [model["id"]],
            },
        )
    assert created.status_code == 201
    run_id = created.get_json()["run"]["id"]
    create_events = [r for r in caplog.records if r.getMessage().startswith("run_created")]
    assert len(create_events) == 1 and create_events[0].name == "evaluator.runs"
    create_message = create_events[0].getMessage()
    assert "case_count=1" in create_message and "model_count=1" in create_message
    assert "total=1" in create_message and "forbidden" not in create_message

    caplog.clear()
    with caplog.at_level(logging.INFO):
        cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    cancel_events = [r for r in caplog.records if r.getMessage().startswith("run_cancelled")]
    assert len(cancel_events) == 1 and cancel_events[0].name == "evaluator.runs"
    assert "done=1" in cancel_events[0].getMessage() and "total=1" in cancel_events[0].getMessage()


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
    output = "\n".join(record.getMessage() for record in records)
    assert marker not in output
    assert "route=/api/v1/endpoints" in output


def test_request_id_is_server_generated_and_run_route_uses_template(app, caplog) -> None:
    client = app.test_client()
    external_id = "external-request-id"
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/api/v1/runs/10000000-0000-4000-8000-000000000001",
            headers={"X-Request-ID": external_id},
        )

    response_id = response.headers["X-Request-ID"]
    UUID(response_id)
    assert response_id != external_id
    records = [record for record in caplog.records if record.name == "evaluator.api.handlers"]
    output = "\n".join(record.getMessage() for record in records)
    assert "route=/api/v1/runs/<uuid:run_id>" in output
    assert "10000000-0000-4000-8000-000000000001" not in output
    assert external_id not in output
    assert records and all(getattr(record, "request_id", "-") == response_id for record in records)
    assert all(
        getattr(record, "run_id", "-") == "10000000-0000-4000-8000-000000000001"
        for record in records
    )


def test_unmatched_path_content_is_not_logged(app, caplog) -> None:
    client = app.test_client()
    marker = "forbidden-path-marker"
    with caplog.at_level(logging.DEBUG):
        response = client.get(f"/api/v1/{marker}?query={marker}")

    assert response.status_code == 404
    records = [record for record in caplog.records if record.name == "evaluator.api.handlers"]
    output = "\n".join(record.getMessage() for record in records)
    assert "route=<unmatched>" in output
    assert marker not in output
