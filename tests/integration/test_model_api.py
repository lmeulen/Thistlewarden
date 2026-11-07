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
            "DATABASE_PATH": str(tmp_path / "models.db"),
            "ENCRYPTION_KEY": key,
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": False},
    )


def _endpoint(client) -> str:
    response = client.post(
        "/api/v1/endpoints",
        json={"name": "Provider", "completions_url": "https://api.example.com/v1/chat/completions"},
    )
    return response.get_json()["endpoint"]["id"]


def _profile(display_name: str = "Deterministic") -> dict[str, object]:
    return {
        "model_id": "provider-model-a",
        "display_name": display_name,
        "description": "Test profile",
        "temperature": "0.00",
        "top_p": "0.95",
        "max_tokens": 2048,
        "reasoning_field_path": "reasoning.effort",
        "reasoning_value": "medium",
        "custom_parameters": {"seed": 42},
        "pricing": {
            "input_per_million": "0.12345678",
            "output_per_million": "1.23456789",
            "cached_input_per_million": "0.01234567",
            "reasoning_per_million": "2.34567890",
            "fixed_per_request": "0.00000001",
            "context_threshold": 100000,
            "high_context_input_per_million": "0.24691356",
            "high_context_output_per_million": "2.46913578",
        },
    }


def test_model_profile_crud_preserves_decimal_strings(app) -> None:
    client = app.test_client()
    endpoint_id = _endpoint(client)

    created = client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=_profile())
    assert created.status_code == 201
    model = created.get_json()["model"]
    profile_id = model["id"]
    assert model["pricing"]["input_per_million"] == "0.12345678"
    assert model["pricing"]["fixed_per_request"] == "1E-8"
    assert model["temperature"] == "0.00"
    assert model["custom_parameters"] == {"seed": 42}

    listed = client.get(f"/api/v1/endpoints/{endpoint_id}/models")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.get_json()["models"]] == [profile_id]

    patched = client.patch(
        f"/api/v1/models/{profile_id}",
        json={"display_name": "Updated", "temperature": None, "max_tokens": None},
    )
    assert patched.status_code == 200
    assert patched.get_json()["model"]["temperature"] is None
    assert patched.get_json()["model"]["max_tokens"] is None

    assert client.get(f"/api/v1/models/{profile_id}").status_code == 200
    assert client.delete(f"/api/v1/models/{profile_id}").status_code == 204
    assert client.get(f"/api/v1/models/{profile_id}").status_code == 404


def test_same_model_id_allows_multiple_unique_profiles(app) -> None:
    client = app.test_client()
    endpoint_id = _endpoint(client)
    assert (
        client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=_profile("Low")).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=_profile("High")).status_code
        == 201
    )

    duplicate = client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=_profile("low"))
    assert duplicate.status_code == 409
    assert len(client.get(f"/api/v1/endpoints/{endpoint_id}/models").get_json()["models"]) == 2


def test_partial_high_context_pricing_is_validation_error(app) -> None:
    client = app.test_client()
    endpoint_id = _endpoint(client)
    payload = _profile()
    payload["pricing"] = {
        "input_per_million": "1",
        "output_per_million": "2",
        "context_threshold": 1000,
        "high_context_input_per_million": "3",
    }

    response = client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_endpoint_deactivation_archives_models_and_delete_is_restricted(app) -> None:
    client = app.test_client()
    endpoint_id = _endpoint(client)
    profile_id = client.post(f"/api/v1/endpoints/{endpoint_id}/models", json=_profile()).get_json()[
        "model"
    ]["id"]

    assert client.delete(f"/api/v1/endpoints/{endpoint_id}").status_code == 409
    assert (
        client.patch(f"/api/v1/endpoints/{endpoint_id}", json={"active": False}).status_code == 200
    )
    profile = client.get(f"/api/v1/models/{profile_id}").get_json()["model"]
    assert profile["active"] is False


def test_unknown_endpoint_or_profile_is_not_found(app) -> None:
    client = app.test_client()
    missing = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/v1/endpoints/{missing}/models").status_code == 404
    assert client.get(f"/api/v1/models/{missing}").status_code == 404
