from __future__ import annotations

import base64
import os
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator import create_app
from evaluator.discovery import DiscoveryCandidate

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "gui.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _csrf(client, path: str) -> str:
    page = client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert match
    return match.group(1)


def _endpoint_form(token: str, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "csrf_token": token,
        "name": "Lokale provider",
        "completions_url": "https://provider.example/v1/chat/completions",
        "models_url": "https://provider.example/v1/models",
        "api_key": "gui-fixture-secret",
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer ",
        "extra_headers": "X-Tenant|openbaar|team-a\nX-Private|geheim|header-secret",
        "timeout_seconds": "30",
        "max_retries": "2",
        "requests_per_minute": "60",
        "tokens_per_minute": "1000",
        "max_concurrency": "2",
        "active": "on",
    }
    data.update(changes)
    return data


def test_gui_renders_english_navigation_and_masks_settings_secrets(app) -> None:
    client = app.test_client()
    page = client.get("/instellingen")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Endpoints and models" in text
    assert "Environment values take precedence" in text
    assert "gui-fixture-secret" not in text
    assert "ENCRYPTION_KEY" in text and "••••••••" in text
    assert "Read-only" in text


def test_gui_csrf_blocks_mutation(app) -> None:
    response = app.test_client().post("/endpoints/nieuw", data=_endpoint_form("invalid"))
    assert response.status_code == 400
    connection = app.extensions["database_factory"].connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] == 0
    finally:
        connection.close()


def test_new_model_can_copy_profile_and_select_discovered_model(app, monkeypatch) -> None:
    endpoint = app.extensions["endpoint_service"].create(
        {
            "name": "Provider",
            "completions_url": "https://provider.example/v1/chat/completions",
            "models_url": "https://provider.example/v1/models",
        }
    )
    source = app.extensions["model_profile_service"].create(
        endpoint.id,
        {
            "model_id": "old-model",
            "display_name": "Productieprofiel",
            "temperature": "0.4",
            "max_tokens": 321,
            "custom_parameters": {"seed": 7},
            "pricing": {"input_per_million": "1.2", "output_per_million": "3.4"},
        },
    )
    monkeypatch.setattr(
        app.extensions["model_discovery_service"],
        "discover",
        lambda endpoint_id: [
            DiscoveryCandidate("new-model", "new-model"),
            DiscoveryCandidate("other-model", "other-model"),
        ],
    )

    client = app.test_client()
    path = (
        f"/endpoints/{endpoint.id}/modellen/nieuw"
        f"?copy_from={source.id}&discover=1&model_id=new-model"
    )
    response = client.get(path)
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Existing model profile" in text
    assert "Available model" in text
    assert 'name="model_id" required value="new-model"' in text
    assert 'name="display_name" required value="Productieprofiel (copy)"' in text
    assert 'name="temperature" value="0.4"' in text
    assert 'name="max_tokens" value="321"' in text
    assert '{"seed": 7}' in text
    assert "1.2" in text and "3.4" in text


def test_new_model_rejects_copy_source_from_other_endpoint(app) -> None:
    service = app.extensions["endpoint_service"]
    first = service.create(
        {"name": "Eerste", "completions_url": "https://first.example/v1/chat/completions"}
    )
    second = service.create(
        {"name": "Tweede", "completions_url": "https://second.example/v1/chat/completions"}
    )
    source = app.extensions["model_profile_service"].create(
        first.id, {"model_id": "m", "display_name": "Niet toegestaan"}
    )

    response = app.test_client().get(f"/endpoints/{second.id}/modellen/nieuw?copy_from={source.id}")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Copy source does not belong to this endpoint" in text
    assert 'name="display_name" required value=""' in text


def test_saved_model_can_be_tested_with_result_metrics(app, monkeypatch) -> None:
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "Provider", "completions_url": "https://provider.example/chat/completions"}
    )
    profile = app.extensions["model_profile_service"].create(
        endpoint.id,
        {
            "model_id": "test-model",
            "display_name": "Testmodel",
            "temperature": "0.3",
            "custom_parameters": {"seed": 9},
        },
    )
    tested = []

    def fake_test(value):
        tested.append(value)
        return SimpleNamespace(
            content="MODEL_TEST_OK",
            successful=True,
            error_code=None,
            finish_reason="stop",
            total_latency_ms=Decimal("125.5"),
            ttft_ms=Decimal("25.25"),
            tokens_per_second=Decimal("8.5"),
            input_tokens=12,
            output_tokens=3,
        )

    monkeypatch.setattr(app.extensions["model_profile_tester"], "test", fake_test)
    client = app.test_client()
    path = f"/modellen/{profile.id}"
    page = client.get(path).get_data(as_text=True)
    assert "Test model" in page and "Save changes first" in page
    assert client.post(f"{path}/testen").status_code == 400

    response = client.post(f"{path}/testen", data={"csrf_token": _csrf(client, path)})
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert tested == [profile]
    assert "Model call succeeded" in text
    assert "MODEL_TEST_OK" in text
    assert "125.50 ms" in text and "25.25 ms" in text
    assert "12 / 3" in text and "stop" in text


def test_failed_model_test_shows_safe_error(app, monkeypatch) -> None:
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "Provider", "completions_url": "https://provider.example/chat/completions"}
    )
    profile = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "test-model", "display_name": "Testmodel"}
    )
    monkeypatch.setattr(
        app.extensions["model_profile_tester"],
        "test",
        lambda value: SimpleNamespace(
            content="",
            successful=False,
            error_code="invalid_stream",
            finish_reason=None,
            total_latency_ms=Decimal("50"),
            ttft_ms=None,
            tokens_per_second=None,
            input_tokens=None,
            output_tokens=None,
        ),
    )
    client = app.test_client()
    path = f"/modellen/{profile.id}"
    response = client.post(f"{path}/testen", data={"csrf_token": _csrf(client, path)})
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Model call failed. Review test result below" in text
    assert "Test result: failed" in text
    assert "Error code" in text and "invalid_stream" in text
    assert "Application request ID" in text
    assert f'action="{path}"' in text

    save_response = client.post(
        path,
        data={
            "csrf_token": _csrf(client, path),
            "model_id": "test-model",
            "display_name": "Aangepast na mislukte test",
            "max_tokens_field_path": "max_completion_tokens",
            "custom_parameters": "{}",
            "input_per_million": "0",
            "output_per_million": "0",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert save_response.status_code == 200
    assert "Model profile saved" in save_response.get_data(as_text=True)
    updated = app.extensions["model_profile_service"].get(profile.id)
    assert updated.data.display_name == "Aangepast na mislukte test"
    assert updated.data.max_tokens_field_path == "max_completion_tokens"


def test_model_test_provider_exception_is_visible(app, monkeypatch) -> None:
    from evaluator.api.errors import ProviderClientError

    endpoint = app.extensions["endpoint_service"].create(
        {"name": "Provider", "completions_url": "https://provider.example/chat/completions"}
    )
    profile = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "gpt-5.6-terra", "display_name": "Terra"}
    )

    def rejected(value):
        raise ProviderClientError()

    monkeypatch.setattr(app.extensions["model_profile_tester"], "test", rejected)
    client = app.test_client()
    path = f"/modellen/{profile.id}"
    response = client.post(f"{path}/testen", data={"csrf_token": _csrf(client, path)})
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "PROVIDER_CLIENT_ERROR" in text
    assert "Provider rejected request" in text
    assert response.headers["X-Request-ID"] in text


def test_unexpected_model_test_exception_is_visible_without_detail(app, monkeypatch) -> None:
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "Provider", "completions_url": "https://provider.example/chat/completions"}
    )
    profile = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "gpt-5.6-terra", "display_name": "Terra"}
    )
    sensitive_detail = "provider-body-must-not-leak"

    def broken(value):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(app.extensions["model_profile_tester"], "test", broken)
    client = app.test_client()
    path = f"/modellen/{profile.id}"
    response = client.post(f"{path}/testen", data={"csrf_token": _csrf(client, path)})
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "INTERNAL_ERROR" in text
    assert "Unexpected error during model test" in text
    assert sensitive_detail not in text


def test_gui_creates_endpoint_preserves_blank_secrets_and_creates_model(app) -> None:
    client = app.test_client()
    token = _csrf(client, "/endpoints/nieuw")
    created = client.post("/endpoints/nieuw", data=_endpoint_form(token), follow_redirects=True)
    text = created.get_data(as_text=True)
    assert created.status_code == 200
    assert "Endpoint created" in text
    assert "gui-fixture-secret" not in text
    assert "header-secret" not in text

    connection = app.extensions["database_factory"].connect()
    try:
        endpoint_id, api_secret = connection.execute(
            "SELECT id,api_key_secret_id FROM endpoints"
        ).fetchone()
        header_secret = connection.execute(
            "SELECT secret_id FROM endpoint_headers WHERE name='X-Private'"
        ).fetchone()[0]
    finally:
        connection.close()

    token = _csrf(client, f"/endpoints/{endpoint_id}")
    edited = client.post(
        f"/endpoints/{endpoint_id}",
        data=_endpoint_form(
            token,
            name="Gewijzigde provider",
            api_key="",
            extra_headers="X-Tenant|openbaar|team-b\nX-Private|geheim|",
        ),
        follow_redirects=True,
    )
    assert edited.status_code == 200
    connection = app.extensions["database_factory"].connect()
    try:
        assert (
            connection.execute("SELECT api_key_secret_id FROM endpoints").fetchone()[0]
            == api_secret
        )
        assert (
            connection.execute(
                "SELECT secret_id FROM endpoint_headers WHERE name='X-Private'"
            ).fetchone()[0]
            == header_secret
        )
    finally:
        connection.close()

    token = _csrf(client, f"/endpoints/{endpoint_id}/modellen/nieuw")
    model = client.post(
        f"/endpoints/{endpoint_id}/modellen/nieuw",
        data={
            "csrf_token": token,
            "model_id": "fixture-model",
            "display_name": "Fixture model",
            "temperature": "0.2",
            "reasoning_field_path": "reasoning.effort",
            "reasoning_value": "high",
            "custom_parameters": '{"seed":42}',
            "input_per_million": "1.25",
            "output_per_million": "3.5",
            "active": "on",
        },
        follow_redirects=True,
    )
    assert model.status_code == 200
    assert "Model profile created" in model.get_data(as_text=True)
    assert "Fixture model" in client.get("/endpoints").get_data(as_text=True)
