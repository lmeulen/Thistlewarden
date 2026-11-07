from __future__ import annotations

from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.config import load_bootstrap_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    settings = load_bootstrap_settings(
        env_file=None,
        environment={
            "DATABASE_PATH": str(tmp_path / "data" / "production.db"),
            "EXPORT_PATH": str(tmp_path / "exports"),
            "LOG_LEVEL": "WARNING",
        },
    )
    return create_app(settings=settings, test_config={"TESTING": True})


def test_app_factory_starts_in_test_mode(app) -> None:
    assert app.testing is True
    assert app.name == "evaluator.app"


def test_health_has_stable_database_independent_contract(app) -> None:
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "service": "thistlewarden",
        "status": "ok",
        "version": "1",
    }


def test_index_renders_dutch_page(app) -> None:
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    page = response.get_data(as_text=True)
    assert "Thistlewarden" in page
    assert "Lokale evaluatie van taalmodellen." in page
    assert '<html lang="nl">' in page


def test_openapi_uses_product_name(app) -> None:
    response = app.test_client().get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.get_json()["info"]["title"] == "Thistlewarden API"


def test_app_start_does_not_create_runtime_files(app, tmp_path: Path) -> None:
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "exports").exists()


def test_app_factory_accepts_container_bind_environment(tmp_path: Path) -> None:
    environment = {
        "CONTAINER_MODE": "true",
        "HOST": "0.0.0.0",
        "DATABASE_PATH": str(tmp_path / "container.db"),
        "EXPORT_PATH": str(tmp_path / "exports"),
        "LOG_LEVEL": "WARNING",
        "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    settings = load_bootstrap_settings(env_file=None, environment=environment)

    container_app = create_app(
        settings=settings,
        settings_environment=environment,
        initialize_database=True,
        test_config={"TESTING": True},
    )

    assert container_app.extensions["settings"].values.host == "0.0.0.0"
    assert container_app.extensions["settings"].values.container_mode is True
    assert (tmp_path / "container.db").exists()
    container_app.extensions["shutdown_services"]()
