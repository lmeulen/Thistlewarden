from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import httpx
import pytest

from evaluator import create_app
from evaluator.endpoints import EndpointService
from evaluator.models import ModelProfileService
from evaluator.provider import OpenAIProviderClient, ProviderHeaderResolver

pytestmark = pytest.mark.integration


def test_encrypted_endpoint_to_exact_provider_wire_without_secret_logs(
    tmp_path: Path, caplog
) -> None:
    app = create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "provider.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "DEBUG",
        },
        initialize_database=True,
    )
    endpoints: EndpointService = app.extensions["endpoint_service"]
    models: ModelProfileService = app.extensions["model_profile_service"]
    endpoint = endpoints.create(
        {
            "name": "Mock",
            "completions_url": "https://mock.test/v1/chat/completions",
            "api_key": "wire-api-secret",
            "api_key_prefix": "Bearer ",
            "extra_headers": [{"name": "X-Tenant", "value": "wire-header-secret", "secret": True}],
        }
    )
    profile = models.create(
        endpoint.id, {"model_id": "model-a", "display_name": "A", "temperature": "0.125"}
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"X-Request-ID": "req-42"},
            json={
                "choices": [{"message": {"content": "private-response"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    resolver = ProviderHeaderResolver(
        endpoints._repository,  # noqa: SLF001 - integration wiring verification
        app.extensions["secret_repository"],
        {},
    )
    client = OpenAIProviderClient(
        resolver, max_response_bytes=10000, transport=httpx.MockTransport(handler)
    )
    with caplog.at_level(logging.DEBUG):
        result = client.complete(
            endpoint, profile.data, prompt="private-prompt", system_prompt="private-system"
        )
    assert result.content == "private-response" and result.provider_request_id == "req-42"
    assert captured["headers"]["authorization"] == "Bearer wire-api-secret"
    assert captured["headers"]["x-tenant"] == "wire-header-secret"
    assert captured["body"] == {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "private-system"},
            {"role": "user", "content": "private-prompt"},
        ],
        "temperature": 0.125,
        "stream": False,
    }
    logs = caplog.text
    for secret in (
        "wire-api-secret",
        "wire-header-secret",
        "private-prompt",
        "private-system",
        "private-response",
    ):
        assert secret not in logs
