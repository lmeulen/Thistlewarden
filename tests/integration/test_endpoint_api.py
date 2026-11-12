from __future__ import annotations

import base64
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
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "endpoint.db"),
            "ENCRYPTION_KEY": key,
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": False},
    )


def _payload(name: str = "Primary") -> dict[str, object]:
    return {
        "name": name,
        "completions_url": "https://api.example.com/v1/chat/completions",
        "models_url": "https://api.example.com/v1/models",
        "api_key": "fixture-api-secret",
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer ",
        "extra_headers": [
            {"name": "X-Tenant", "value": "tenant-one", "secret": False},
            {"name": "X-Private", "value": "fixture-header-secret", "secret": True},
        ],
        "timeout_seconds": 30,
        "max_retries": 3,
        "requests_per_minute": 60,
        "tokens_per_minute": 10000,
        "max_concurrency": 4,
        "active": True,
    }


def test_endpoint_crud_masks_secrets_and_keeps_plaintext_out_of_database(app) -> None:
    client = app.test_client()
    created = client.post("/api/v1/endpoints", json=_payload())

    assert created.status_code == 201
    endpoint = created.get_json()["endpoint"]
    endpoint_id = endpoint["id"]
    assert endpoint["api_key"] == "********"
    headers = {item["name"]: item for item in endpoint["extra_headers"]}
    assert headers["X-Private"] == {"name": "X-Private", "value": "********", "secret": True}
    assert headers["X-Tenant"]["value"] == "tenant-one"
    response_text = created.get_data(as_text=True)
    assert "fixture-api-secret" not in response_text
    assert "fixture-header-secret" not in response_text

    listed = client.get("/api/v1/endpoints")
    assert listed.status_code == 200
    assert len(listed.get_json()["endpoints"]) == 1
    fetched = client.get(f"/api/v1/endpoints/{endpoint_id}")
    assert fetched.status_code == 200

    database_path = app.extensions["database_factory"].database_path
    database_bytes = database_path.read_bytes()
    assert b"fixture-api-secret" not in database_bytes
    assert b"fixture-header-secret" not in database_bytes

    deleted = client.delete(f"/api/v1/endpoints/{endpoint_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/endpoints/{endpoint_id}").status_code == 404


def test_patch_without_or_with_blank_api_key_preserves_existing_secret(app) -> None:
    client = app.test_client()
    endpoint_id = client.post("/api/v1/endpoints", json=_payload()).get_json()["endpoint"]["id"]
    factory = app.extensions["database_factory"]

    connection = factory.connect()
    try:
        original_id = connection.execute("SELECT api_key_secret_id FROM endpoints").fetchone()[0]
    finally:
        connection.close()

    assert (
        client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"name": "Renamed"}).status_code
        == 200
    )
    assert client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"api_key": ""}).status_code == 200
    connection = factory.connect()
    try:
        preserved_id = connection.execute("SELECT api_key_secret_id FROM endpoints").fetchone()[0]
    finally:
        connection.close()
    assert preserved_id == original_id

    replacement = client.patch(
        f"/api/v1/endpoints/{endpoint_id}", json={"api_key": "replacement-api-secret"}
    )
    assert replacement.status_code == 200
    connection = factory.connect()
    try:
        new_id = connection.execute("SELECT api_key_secret_id FROM endpoints").fetchone()[0]
    finally:
        connection.close()
    assert new_id != original_id
    assert b"replacement-api-secret" not in factory.database_path.read_bytes()


def test_secret_header_replacement_and_plain_header_round_trip(app) -> None:
    client = app.test_client()
    endpoint_id = client.post("/api/v1/endpoints", json=_payload()).get_json()["endpoint"]["id"]

    response = client.patch(
        f"/api/v1/endpoints/{endpoint_id}",
        json={
            "extra_headers": [
                {"name": "X-New-Private", "value": "new-header-secret", "secret": True},
                {"name": "X-Project", "value": "project-a", "secret": False},
            ]
        },
    )

    assert response.status_code == 200
    headers = {item["name"]: item for item in response.get_json()["endpoint"]["extra_headers"]}
    assert headers["X-New-Private"]["value"] == "********"
    assert headers["X-Project"]["value"] == "project-a"
    assert "new-header-secret" not in response.get_data(as_text=True)


def test_duplicate_name_is_conflict_and_does_not_leave_orphan_secret(app) -> None:
    client = app.test_client()
    assert client.post("/api/v1/endpoints", json=_payload("Same Name")).status_code == 201
    conflict = client.post("/api/v1/endpoints", json=_payload("same name"))

    assert conflict.status_code == 409
    connection = app.extensions["database_factory"].connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM secret_records").fetchone()[0] == 2
    finally:
        connection.close()


def test_validation_contract_for_http_and_limits(app) -> None:
    client = app.test_client()
    unsafe = _payload()
    unsafe["completions_url"] = "http://external.example/v1"
    response = client.post("/api/v1/endpoints", json=unsafe)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"

    unsafe["trusted_internal_http"] = True
    unsafe["security_warning_confirmed"] = True
    assert client.post("/api/v1/endpoints", json=unsafe).status_code == 201


def test_unknown_endpoint_and_malformed_uuid_are_safe_errors(app) -> None:
    client = app.test_client()
    assert client.get("/api/v1/endpoints/00000000-0000-0000-0000-000000000001").status_code == 404
    malformed = client.get("/api/v1/endpoints/not-a-uuid")
    assert malformed.status_code == 404
    assert malformed.content_type.startswith("application/json")
