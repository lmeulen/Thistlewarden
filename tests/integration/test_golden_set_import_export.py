from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import pytest

from evaluator import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "io.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
            "MAX_IMPORT_FILE_BYTES": "100000",
        },
        initialize_database=True,
    )


def _create_example(client):
    created = client.post(
        "/api/v1/golden-sets",
        json={
            "name": "Voorbeeld é",
            "description": "beschrijving",
            "version": "2.0",
            "tags": ["set"],
        },
    )
    set_id = created.get_json()["golden_set"]["id"]
    item = {
        "case_id": "case-1",
        "prompt": "regel 1\nregel 2",
        "system_prompt": "sys",
        "expected_answer": "4",
        "evaluation_method": "exact_match",
        "evaluator_config": {"trim": True, "case_sensitive": False},
        "category": "rekenen",
        "tags": ["basis"],
        "weight": "1.2500",
        "enabled": False,
    }
    assert client.post(f"/api/v1/golden-sets/{set_id}/items", json=item).status_code == 201
    return set_id


@pytest.mark.parametrize("format_", ["json", "csv"])
def test_export_delete_import_round_trip_and_download_headers(app, format_: str) -> None:
    client = app.test_client()
    set_id = _create_example(client)
    exported = client.get(f"/api/v1/golden-sets/{set_id}/export?format={format_}")
    assert exported.status_code == 200
    assert "attachment;" in exported.headers["Content-Disposition"]
    assert f".{format_}" in exported.headers["Content-Disposition"]
    assert "filename*=UTF-8''" in exported.headers["Content-Disposition"]
    assert client.delete(f"/api/v1/golden-sets/{set_id}").status_code == 204
    data = {"file": (io.BytesIO(exported.data), f"set.{format_}")}
    if format_ == "csv":
        data["name"] = "Voorbeeld é"
    imported = client.post(
        f"/api/v1/golden-sets/import?format={format_}",
        data=data,
        content_type="multipart/form-data",
    )
    assert imported.status_code == 201
    new_id = imported.get_json()["golden_set"]["id"]
    items = client.get(f"/api/v1/golden-sets/{new_id}/items").get_json()["items"]
    assert len(items) == 1
    assert items[0]["prompt"] == "regel 1\nregel 2"
    assert items[0]["weight"] == "1.2500"
    assert items[0]["enabled"] is False


def test_preview_writes_nothing_and_invalid_row_is_atomic(app) -> None:
    client = app.test_client()
    document = {
        "name": "Preview",
        "items": [
            {
                "case_id": "one",
                "prompt": "p",
                "expected_answer": "x",
                "evaluation_method": "exact_match",
                "evaluator_config": {},
            }
        ],
    }
    preview = client.post(
        "/api/v1/golden-sets/import?format=json&preview=true",
        data={"file": (io.BytesIO(json.dumps(document).encode()), "set.json")},
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    assert client.get("/api/v1/golden-sets").get_json()["pagination"]["total"] == 0
    document["items"].append(
        {
            "case_id": "bad",
            "prompt": "",
            "evaluation_method": "regex",
            "evaluator_config": {"pattern": "["},
        }
    )
    failed = client.post(
        "/api/v1/golden-sets/import?format=json",
        data={"file": (io.BytesIO(json.dumps(document).encode()), "set.json")},
        content_type="multipart/form-data",
    )
    assert failed.status_code == 400
    details = failed.get_json()["error"]["details"]
    assert any("row 2 (bad)" in item["field"] for item in details)
    assert client.get("/api/v1/golden-sets").get_json()["pagination"]["total"] == 0


def test_duplicate_case_id_and_oversized_file_rejected_before_write(app) -> None:
    client = app.test_client()
    item = {
        "case_id": "Same",
        "prompt": "p",
        "expected_answer": "x",
        "evaluation_method": "exact_match",
        "evaluator_config": {},
    }
    document = {"name": "Duplicate", "items": [item, {**item, "case_id": "same"}]}
    duplicate = client.post(
        "/api/v1/golden-sets/import?format=json",
        data={"file": (io.BytesIO(json.dumps(document).encode()), "set.json")},
        content_type="multipart/form-data",
    )
    assert duplicate.status_code == 400
    assert "dupliceert" in str(duplicate.get_json())
    oversized = client.post(
        "/api/v1/golden-sets/import?format=json",
        data={"file": (io.BytesIO(b"x" * 100001), "big.json")},
        content_type="multipart/form-data",
    )
    assert oversized.status_code == 400
    assert client.get("/api/v1/golden-sets").get_json()["pagination"]["total"] == 0
