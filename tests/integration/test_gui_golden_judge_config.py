from __future__ import annotations

import base64
import io
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "gui-step27.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _csrf(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def _create_set(client) -> str:
    token = _csrf(client, "/golden-sets/nieuw")
    response = client.post(
        "/golden-sets/nieuw",
        data={
            "csrf_token": token,
            "name": "GUI set",
            "version": "1.0",
            "tags": "gui, test",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return response.headers["Location"].rsplit("/", 1)[-1]


def test_step27_empty_pages_render_and_navigation(app) -> None:
    client = app.test_client()
    for path, text in (
        ("/golden-sets", "Golden sets"),
        ("/judges", "Judge profiles"),
        ("/configuratie", "Configuration exchange"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.get_data(as_text=True)
    assert "Configuration" in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize(
    ("method", "expected", "config"),
    [
        ("exact_match", "ja", {}),
        ("contains", None, {"expected_fragments": ["ja"], "match_mode": "all"}),
        ("regex", None, {"pattern": "^ja$"}),
        ("numeric", None, {"expected_value": "42", "absolute_tolerance": "0.1"}),
        ("json_schema", None, {"schema": {"type": "object"}}),
        ("llm_judge", None, {"rubric": [{"id": "correct", "description": "Juist", "weight": "1"}]}),
    ],
)
def test_gui_saves_every_evaluator_method(
    app, method: str, expected: str | None, config: dict
) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}/items/nieuw")
    response = client.post(
        f"/golden-sets/{set_id}/items/nieuw",
        data={
            "csrf_token": token,
            "case_id": f"case-{method}",
            "prompt": "Antwoord.",
            "expected_answer": expected or "",
            "evaluation_method": method,
            "evaluator_config": json.dumps(config),
            "weight": "1.5",
            "enabled": "on",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Test case created" in response.get_data(as_text=True)


def test_duplicate_set_and_quick_delete_item(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    service = app.extensions["golden_set_service"]
    original = service.create_item(
        UUID(set_id),
        {
            "case_id": "duplicate-me",
            "prompt": "Answer yes.",
            "expected_answer": "yes",
            "evaluation_method": "exact_match",
        },
    )
    app.extensions["quality_gate_service"].set_defaults(UUID(set_id), {"min_pass_rate": "0.9"})

    listing = client.get("/golden-sets").get_data(as_text=True)
    assert "Duplicate" in listing and "duplicate-set-form" in listing
    token = _csrf(client, "/golden-sets")
    response = client.post(
        f"/golden-sets/{set_id}/dupliceren",
        data={"csrf_token": token, "name": "GUI set kopie"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    duplicate_id = response.headers["Location"].rsplit("/", 1)[-1]
    duplicate = service.get_set(UUID(duplicate_id))
    duplicate_items, total = service.list_items(
        duplicate.id, page=1, per_page=100, category=None, tag=None, enabled=None
    )
    assert duplicate.data.name == "GUI set kopie"
    assert total == 1 and duplicate_items[0].data.case_id == "duplicate-me"
    assert app.extensions["quality_gate_service"].defaults(duplicate.id).min_pass_rate == Decimal(
        "0.9"
    )

    second = service.create_item(
        UUID(set_id),
        {
            "case_id": "keep-me",
            "prompt": "Answer no.",
            "expected_answer": "no",
            "evaluation_method": "exact_match",
        },
    )
    page = client.get(f"/golden-sets/{set_id}").get_data(as_text=True)
    assert "inline-delete-form" in page and ">Delete</button>" in page
    assert page.count('name="item_ids"') == 2
    assert "Delete selection" in page
    token = _csrf(client, f"/golden-sets/{set_id}")
    deleted = client.post(
        f"/golden-sets/{set_id}/items/verwijderen",
        data={"csrf_token": token, "item_ids": [str(original.id), str(second.id)]},
        follow_redirects=True,
    )
    assert deleted.status_code == 200 and "2 test case(s) deleted" in deleted.get_data(as_text=True)
    assert (
        service.list_items(
            UUID(set_id), page=1, per_page=100, category=None, tag=None, enabled=None
        )[1]
        == 0
    )


def test_quality_gates_and_golden_json_import_preview(app) -> None:
    client = app.test_client()
    set_id = _create_set(client)
    token = _csrf(client, f"/golden-sets/{set_id}")
    response = client.post(
        f"/golden-sets/{set_id}/gates",
        data={"csrf_token": token, "min_pass_rate": "0.9", "max_total_cost": "2.5"},
        follow_redirects=True,
    )
    assert response.status_code == 200 and "Quality gates saved" in response.get_data(as_text=True)

    exported = client.get(f"/golden-sets/{set_id}/download/json")
    token = _csrf(client, "/golden-sets/import")
    preview = client.post(
        "/golden-sets/import",
        data={
            "csrf_token": token,
            "format": "json",
            "file": (io.BytesIO(exported.data), "set.json"),
        },
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    assert "Valid preview" in preview.get_data(as_text=True)


def test_judge_form_and_secret_free_configuration_preview(app) -> None:
    client = app.test_client()
    endpoint = client.post(
        "/api/v1/endpoints",
        json={
            "name": "Provider",
            "completions_url": "https://example.test/v1/chat/completions",
            "api_key": "never-export-this",
        },
    ).get_json()["endpoint"]
    model = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={"model_id": "m1", "display_name": "Model een"},
    ).get_json()["model"]
    token = _csrf(client, "/judges/nieuw")
    judge = client.post(
        "/judges/nieuw",
        data={
            "csrf_token": token,
            "name": "Hoofdjudge",
            "model_profile_id": model["id"],
            "prompt_template": "judge-v1",
            "template_version": "1.0",
            "threshold": "0.7",
            "temperature": "0",
            "rubric": "correct|1|Juist antwoord",
            "is_global_default": "on",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert judge.status_code == 200 and "Judge profile created" in judge.get_data(as_text=True)

    exported = client.get("/configuratie/export")
    assert exported.status_code == 200
    assert b"never-export-this" not in exported.data
    bundle = json.loads(exported.data)
    assert bundle["schema_version"] == "1.0"
    token = _csrf(client, "/configuratie")
    preview = client.post(
        "/configuratie/import",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(exported.data), "config.json"),
        },
        content_type="multipart/form-data",
    )
    text = preview.get_data(as_text=True)
    assert preview.status_code == 200 and "Import preview" in text
    assert "Endpoints with missing secrets" in text
    assert 'name="preview_payload"' in text
    assert "Import preview" in text

    token = _csrf(client, "/configuratie")
    privacy_browser_preview = client.post(
        "/configuratie/import",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(exported.data), "config.json"),
        },
        content_type="multipart/form-data",
        headers={"Origin": "null"},
    )
    assert privacy_browser_preview.status_code == 200
    assert "Import preview" in privacy_browser_preview.get_data(as_text=True)


def test_configuration_preview_can_be_committed_without_reselecting_file(app) -> None:
    client = app.test_client()
    bundle = {
        "schema_version": "1.0",
        "endpoints": [],
        "model_profiles": [],
        "judge_profiles": [],
        "golden_sets": [],
        "golden_set_items": [],
    }
    token = _csrf(client, "/configuratie")
    preview = client.post(
        "/configuratie/import",
        data={
            "csrf_token": token,
            "mode": "merge",
            "file": (io.BytesIO(json.dumps(bundle).encode()), "config.json"),
        },
        content_type="multipart/form-data",
    )
    text = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert 'name="preview_payload"' in text

    token = _csrf(client, "/configuratie")
    committed = client.post(
        "/configuratie/import",
        data={
            "csrf_token": token,
            "mode": "merge",
            "preview_payload": json.dumps(bundle),
            "commit": "",
        },
        follow_redirects=True,
    )
    assert committed.status_code == 200
    assert "Configuration imported atomically" in committed.get_data(as_text=True)
