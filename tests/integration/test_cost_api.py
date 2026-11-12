from __future__ import annotations

import base64
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
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "costs.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )


def _profile(client) -> str:
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Provider", "completions_url": "https://api.example.com/v1/chat/completions"},
    ).get_json()["endpoint"]
    return client.post(
        f"/api/v1/endpoints/{endpoint['id']}/models",
        json={
            "model_id": "model-a",
            "display_name": "Cost profile",
            "pricing": {
                "input_per_million": "1.123456789",
                "output_per_million": "2.987654321",
                "cached_input_per_million": "0.5",
                "reasoning_per_million": "3",
                "fixed_per_request": "0.01",
            },
        },
    ).get_json()["model"]["id"]


def test_database_profile_usage_produces_immutable_precise_cost_record(app) -> None:
    client = app.test_client()
    profile_id = _profile(client)
    response = client.post(
        f"/api/v1/models/{profile_id}/cost-calculations",
        json={
            "chargeable": True,
            "usage": {
                "input_tokens": 1000000,
                "output_tokens": 500000,
                "cached_input_tokens": 200000,
                "reasoning_tokens": 100000,
            },
        },
    )
    assert response.status_code == 201
    cost = response.get_json()["cost_calculation"]
    assert cost["input_cost"] == "0.8987654312"
    assert cost["cached_input_cost"] == "0.10000000"
    assert cost["total_cost"] == "2.5038271596"
    assert cost["pricing"]["input_per_million"] == "1.123456789"

    assert (
        client.patch(
            f"/api/v1/models/{profile_id}",
            json={"pricing": {"input_per_million": "99", "output_per_million": "99"}},
        ).status_code
        == 200
    )
    stored = client.get(f"/api/v1/cost-calculations/{cost['id']}").get_json()["cost_calculation"]
    assert stored == cost


def test_missing_subset_usage_is_persisted_as_unknown(app) -> None:
    client = app.test_client()
    profile_id = _profile(client)
    response = client.post(
        f"/api/v1/models/{profile_id}/cost-calculations",
        json={"chargeable": True, "usage": {"input_tokens": 10, "output_tokens": 2}},
    )
    assert response.status_code == 201
    cost = response.get_json()["cost_calculation"]
    assert cost["status"] == "unknown"
    assert cost["reason"] == "required_usage_missing"
    assert cost["total_cost"] is None
    assert cost["fixed_cost"] == "0.01000000"


def test_cost_api_validates_usage_and_missing_profile(app) -> None:
    client = app.test_client()
    profile_id = _profile(client)
    assert (
        client.post(
            f"/api/v1/models/{profile_id}/cost-calculations",
            json={"chargeable": False, "usage": {"input_tokens": -1}},
        ).status_code
        == 400
    )
    missing = "00000000-0000-0000-0000-000000000001"
    assert (
        client.post(
            f"/api/v1/models/{missing}/cost-calculations",
            json={"chargeable": False, "usage": {}},
        ).status_code
        == 404
    )
