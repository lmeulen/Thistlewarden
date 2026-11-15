from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from tests.integration.test_run_queue import fixture_app

from evaluator.api import routes
from evaluator.api.errors import NotFoundError
from evaluator.run_exports import RunExportService

pytestmark = pytest.mark.integration


def _created_run(app: Any) -> UUID:
    golden = app.extensions["golden_set_service"].list_sets(1, 10)[0][0]
    endpoint = app.extensions["endpoint_service"].list_all()[0]
    model = app.extensions["model_profile_service"].list_for_endpoint(endpoint.id)[0]
    return app.extensions["run_queue"].create_run(
        {
            "name": "Coverage / report",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id)],
        }
    )


def test_api_query_and_payload_validation_edges(tmp_path: Path) -> None:
    app, golden, _ = fixture_app(tmp_path)
    client = app.test_client()

    checks = [
        client.post("/api/v1/configuration/import?mode=invalid", json={}),
        client.post("/api/v1/configuration/import?preview=maybe", json={}),
        client.post("/api/v1/configuration/import", json=[]),
        client.get("/api/v1/golden-sets?page=nope"),
        client.get(f"/api/v1/golden-sets/{golden.id}/items?enabled=maybe"),
        client.post("/api/v1/golden-sets/import?format=xml"),
        client.post("/api/v1/golden-sets/import?preview=maybe"),
        client.post("/api/v1/golden-sets/import"),
        client.post("/api/v1/runs/retention-cleanup", json={"retention_days": True}),
        client.post(f"/api/v1/runs/{uuid4()}/rerun-failures", json={"name": " "}),
    ]
    assert all(response.status_code == 400 for response in checks)


def test_configuration_gates_aggregates_and_run_export_routes(tmp_path: Path) -> None:
    app, golden, _ = fixture_app(tmp_path)
    client = app.test_client()
    run_id = _created_run(app)

    exported = client.get("/api/v1/configuration/export")
    assert exported.status_code == 200 and exported.get_json()["schema_version"] == "1.0"
    preview = client.post(
        "/api/v1/configuration/import?mode=merge&preview=true", json=exported.get_json()
    )
    assert preview.status_code == 400
    assert preview.get_json()["error"]["code"] == "VALIDATION_ERROR"

    gates = client.get(f"/api/v1/golden-sets/{golden.id}/quality-gates")
    assert gates.status_code == 200
    updated = client.put(
        f"/api/v1/golden-sets/{golden.id}/quality-gates",
        json={"min_pass_rate": "0.5"},
    )
    assert updated.get_json()["quality_gates"]["min_pass_rate"] == "0.5"
    assert client.get(f"/api/v1/runs/{run_id}/aggregates").status_code == 200
    assert client.get(f"/api/v1/runs/{run_id}/quality-gates").status_code == 200

    for format_, mimetype in (
        ("json", "application/json"),
        ("csv", "text/csv"),
        ("html", "text/html"),
    ):
        response = client.get(f"/api/v1/runs/{run_id}/export?format={format_}")
        assert response.status_code == 200 and response.content_type.startswith(mimetype)
        assert "Coverage-report" in response.headers["Content-Disposition"]
    assert client.get(f"/api/v1/runs/{run_id}/export?format=xml").status_code == 400


def test_run_export_service_formats_outcomes_and_deletes_terminal_run(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    queue = app.extensions["run_queue"]
    run_id = _created_run(app)
    lease = queue.claim("coverage")
    assert lease is not None
    queue.fail(
        lease,
        "blocked",
        {
            "outcome_category": "provider_guardrail",
            "outcome_stage": "target",
            "outcome_code": "blocked",
            "provider_evidence": {"finish_reason": "content_filter"},
        },
    )
    queue.refresh_run_status(run_id)
    service = RunExportService(app.extensions["database_factory"])

    data = service.data(run_id)
    assert data["results"][0]["outcome_category"] == "provider_guardrail"
    assert json.loads(data["results"][0]["provider_evidence_json"])["finish_reason"] == (
        "content_filter"
    )
    assert json.loads(service.export_json(run_id))["run"]["id"] == str(run_id)
    assert service.export_csv(run_id).startswith(b"\xef\xbb\xbfjob_id")
    assert b"Content-Security-Policy" in service.export_html(run_id)

    service.delete(run_id)
    with pytest.raises(NotFoundError):
        service.data(run_id)


@pytest.mark.parametrize(
    ("extension", "accessor"),
    [
        ("endpoint_service", routes._endpoint_service),
        ("golden_set_service", routes._golden_set_service),
        ("model_discovery_service", routes._model_discovery_service),
        ("cost_service", routes._cost_service),
        ("judge_profile_service", routes._judge_profile_service),
        ("model_profile_service", routes._model_profile_service),
        ("run_comparison_service", routes._run_comparison_service),
        ("run_export_service", routes._run_export_service),
        ("configuration_exchange_service", routes._configuration_exchange_service),
        ("quality_gate_service", routes._quality_gate_service),
        ("aggregate_service", routes._aggregate_service),
        ("run_queue", routes._run_queue),
    ],
)
def test_api_service_accessors_reject_broken_application_wiring(
    tmp_path: Path, extension: str, accessor: Callable[[], object]
) -> None:
    app, _, _ = fixture_app(tmp_path)
    app.extensions[extension] = object()
    with app.app_context(), pytest.raises(RuntimeError, match="unavailable"):
        accessor()


def test_api_cancel_cleanup_and_delete_paths(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    client = app.test_client()
    run_id = _created_run(app)

    cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancelled.get_json()["run"]["status"] == "cancelled"
    cleanup = client.post("/api/v1/runs/retention-cleanup", json={"retention_days": 0})
    assert cleanup.get_json() == {"deleted": 0}
    deleted = client.delete(f"/api/v1/runs/{run_id}")
    assert deleted.status_code == 204
