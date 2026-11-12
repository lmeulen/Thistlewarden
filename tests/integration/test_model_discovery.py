from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.api.errors import ValidationApiError
from evaluator.discovery import ModelDiscoveryService
from evaluator.endpoints import EndpointService
from evaluator.models import ModelProfileService

pytestmark = pytest.mark.integration


class FakeDiscoveryClient:
    def __init__(self, models: list[str]) -> None:
        self.models = models

    def discover(self, endpoint):
        return list(self.models)


def test_preview_names_and_selective_atomic_import_preserve_existing(tmp_path: Path) -> None:
    app = create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "discovery.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )
    endpoints: EndpointService = app.extensions["endpoint_service"]
    profiles: ModelProfileService = app.extensions["model_profile_service"]
    endpoint = endpoints.create(
        {
            "name": "Provider",
            "completions_url": "https://mock.test/v1/chat/completions",
            "models_url": "https://mock.test/v1/models",
        }
    )
    existing = profiles.create(
        endpoint.id, {"model_id": "old-id", "display_name": "model-a", "temperature": "0.5"}
    )
    runtime = app.extensions["model_discovery_service"]
    service = ModelDiscoveryService(
        runtime._endpoints,
        runtime._models,  # noqa: SLF001
        FakeDiscoveryClient(["model-a", "model-b"]),
        runtime._executor,
    )  # type: ignore[arg-type]  # noqa: SLF001
    preview = service.discover(endpoint.id)
    assert [(item.model_id, item.suggested_display_name) for item in preview] == [
        ("model-a", "model-a (2)"),
        ("model-b", "model-b"),
    ]
    imported = service.import_selected(endpoint.id, {"model_ids": ["model-a"]})
    assert len(imported) == 1
    assert imported[0].data.model_id == "model-a"
    assert imported[0].data.display_name == "model-a (2)"
    unchanged = profiles.get(existing.id)
    assert unchanged.data.model_id == "old-id" and str(unchanged.data.temperature) == "0.5"
    before = len(profiles.list_for_endpoint(endpoint.id))
    with pytest.raises(ValidationApiError):
        service.import_selected(endpoint.id, {"model_ids": ["not-discovered"]})
    assert len(profiles.list_for_endpoint(endpoint.id)) == before
