from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app
from evaluator.api.errors import ErrorDetail, ValidationApiError
from evaluator.api.openapi import build_openapi_document
from evaluator.config import load_bootstrap_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    settings = load_bootstrap_settings(
        env_file=None,
        environment={
            "DATABASE_PATH": str(tmp_path / "unused.db"),
            "LOG_LEVEL": "WARNING",
            "SECRET_KEY": "fixture-secret-with-at-least-32-bytes",
        },
    )
    app = create_app(settings=settings, test_config={"TESTING": False})

    @app.get("/api/v1/_test/validation")
    def force_validation():
        raise ValidationApiError(
            "fixture-secret-with-at-least-32-bytes is invalid",
            details=(ErrorDetail("name", "fixture-secret-with-at-least-32-bytes is not allowed"),),
        )

    @app.post("/api/v1/_test/internal")
    def force_internal():
        raise RuntimeError("private body fixture-secret-with-at-least-32-bytes")

    return app


def _assert_request_id(response) -> str:
    request_id = response.headers["X-Request-ID"]
    UUID(request_id)
    assert response.get_json()["error"]["request_id"] == request_id
    return request_id


def test_unknown_api_route_has_uniform_json_error(app) -> None:
    response = app.test_client().get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.content_type.startswith("application/json")
    assert response.get_json()["error"] == {
        "code": "NOT_FOUND",
        "message": "API route not found.",
        "details": [],
        "request_id": response.headers["X-Request-ID"],
    }
    _assert_request_id(response)


def test_safe_domain_error_is_sanitized_and_has_request_id(app) -> None:
    response = app.test_client().get("/api/v1/_test/validation")
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "fixture-secret" not in body
    assert body.count("[REDACTED]") == 2
    assert response.get_json()["error"]["details"][0]["field"] == "name"
    _assert_request_id(response)


def test_unexpected_exception_leaks_no_stacktrace_body_or_secret(app, caplog) -> None:
    response = app.test_client().post(
        "/api/v1/_test/internal",
        data="private request body fixture-secret",
        content_type="text/plain",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "RuntimeError" not in body
    assert "private body" not in body
    assert "fixture-secret" not in body
    assert response.get_json()["error"]["code"] == "INTERNAL_ERROR"
    _assert_request_id(response)
    assert "fixture-secret" not in caplog.text
    assert "private body" not in caplog.text


def test_successful_api_response_also_has_request_id_header(app) -> None:
    response = app.test_client().get("/api/v1/health")

    assert response.status_code == 200
    UUID(response.headers["X-Request-ID"])
    assert response.get_json()["status"] == "ok"


def test_settings_endpoint_never_exposes_secret_values(app) -> None:
    response = app.test_client().get("/api/v1/settings")

    assert response.status_code == 200
    assert "fixture-secret" not in response.get_data(as_text=True)
    settings = {item["name"]: item for item in response.get_json()["settings"]}
    assert settings["secret_key"]["secret"] is True
    assert settings["secret_key"]["value"] is None
    assert settings["secret_key"]["lifecycle"] == "bootstrap"
    assert settings["secret_key"]["read_only"] is True


def test_settings_endpoint_distinguishes_active_and_desired_values(app) -> None:
    app.extensions["settings_service"].set_database_value("max_model_profiles_per_run", "12")

    response = app.test_client().get("/api/v1/settings")
    settings = {item["name"]: item for item in response.get_json()["settings"]}
    profile_limit = settings["max_model_profiles_per_run"]

    assert profile_limit["value"] == 20
    assert profile_limit["active_value"] == 20
    assert profile_limit["desired_value"] == 12
    assert profile_limit["lifecycle"] == "restart_required"
    assert profile_limit["pending_restart"] is True


def test_openapi_document_is_valid_shape_and_contains_all_api_routes(app) -> None:
    response = app.test_client().get("/api/v1/openapi.json")
    document = response.get_json()

    assert response.status_code == 200
    assert document == build_openapi_document()
    assert document["openapi"] == "3.1.0"
    assert {"openapi", "info", "paths", "components"} <= document.keys()
    documented = set(document["paths"])
    implemented = {
        re.sub(r"<(?:(?:[^:>]+):)?([^>]+)>", r"{\1}", rule.rule)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/v1/") and "/_test/" not in rule.rule
    }
    assert documented == implemented
    assert "/api/v1/health" in documented
    assert "/api/v1/settings" in documented
