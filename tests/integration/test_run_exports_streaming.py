from __future__ import annotations

import json
import os
import stat
import tracemalloc
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from tests.integration.test_run_queue import fixture_app

from evaluator.api.errors import ValidationApiError
from evaluator.run_exports import RunExportService

pytestmark = pytest.mark.integration


def _run_with_results(app: Any) -> UUID:
    endpoints = app.extensions["endpoint_service"].list_all()
    endpoint = endpoints[0]
    models = [app.extensions["model_profile_service"].list_for_endpoint(endpoint.id)[0]]
    for index in range(2):
        models.append(
            app.extensions["model_profile_service"].create(
                endpoint.id,
                {"model_id": f"batch-{index}", "display_name": f"Batch {index}"},
            )
        )
    golden = app.extensions["golden_set_service"].list_sets(1, 10)[0][0]
    run_id = app.extensions["run_queue"].create_run(
        {
            "name": "Batch <report> & deterministic",
            "golden_set_id": str(golden.id),
            "model_profile_ids": [str(model.id) for model in models],
        }
    )
    queue = app.extensions["run_queue"]
    for index in range(3):
        lease = queue.claim(f"export-{index}")
        assert lease is not None
        queue.fail(
            lease,
            "batch_error",
            {
                "outcome_category": "processing_error",
                "outcome_stage": "evaluator",
                "outcome_code": "batch_error",
                "provider_evidence": {"finish_reason": f"stop<&{index}"},
            },
        )
    queue.refresh_run_status(run_id)
    return run_id


def _read_and_cleanup(service: RunExportService, run_id: UUID, format_: str) -> bytes:
    artifact = service.create_export(run_id, format_)  # type: ignore[arg-type]
    try:
        if os.name == "posix":
            assert stat.S_IMODE(artifact.path.stat().st_mode) & 0o077 == 0
        return artifact.path.read_bytes()
    finally:
        artifact.cleanup()
        assert not artifact.path.exists()


def test_exports_cross_batches_without_materializing_and_are_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = fixture_app(tmp_path)
    run_id = _run_with_results(app)
    service = RunExportService(
        app.extensions["database_factory"], batch_size=1, temporary_directory=tmp_path / "exports"
    )

    def reject_materialization(run_id: UUID) -> dict[str, Any]:
        del run_id
        raise AssertionError("exporter called data()")

    monkeypatch.setattr(service, "data", reject_materialization)
    first_json = _read_and_cleanup(service, run_id, "json")
    second_json = _read_and_cleanup(service, run_id, "json")
    csv_content = _read_and_cleanup(service, run_id, "csv")
    html_content = _read_and_cleanup(service, run_id, "html")

    assert first_json == second_json
    payload = json.loads(first_json)
    assert len(payload["results"]) == 3
    assert [item["job_id"] for item in payload["results"]] == sorted(
        item["job_id"] for item in payload["results"]
    )
    assert csv_content.startswith(b"\xef\xbb\xbfjob_id")
    assert csv_content.count(b"\n") == 4
    assert b"Batch &lt;report&gt; &amp; deterministic" in html_content
    assert b"stop&lt;&amp;0" in html_content
    assert b"Complete data" not in html_content
    assert b'"schema_version"' not in html_content


def test_content_heavy_json_export_stays_within_peak_memory_budget(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    run_id = _run_with_results(app)
    connection = app.extensions["database_factory"].connect()
    try:
        payload = json.dumps(
            {
                "outcome_category": "processing_error",
                "outcome_stage": "evaluator",
                "outcome_code": "large_diagnostic",
                "diagnostic": "x" * (1024 * 1024),
            }
        )
        connection.execute(
            "UPDATE run_results SET result_json=? WHERE run_id=?", (payload, str(run_id))
        )
        connection.commit()
    finally:
        connection.close()
    service = RunExportService(
        app.extensions["database_factory"], batch_size=1, temporary_directory=tmp_path / "exports"
    )

    tracemalloc.start()
    artifact = service.create_export(run_id, "json")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    try:
        assert artifact.path.stat().st_size > 3 * 1024 * 1024
        assert peak < 8 * 1024 * 1024
    finally:
        artifact.cleanup()


def test_export_limit_rejects_before_temporary_file_creation(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    run_id = _run_with_results(app)
    directory = tmp_path / "exports"
    service = RunExportService(
        app.extensions["database_factory"], result_limit=2, temporary_directory=directory
    )

    with pytest.raises(ValidationApiError, match="3 jobs.*at most 2"):
        service.create_export(run_id, "json")
    assert not directory.exists() or not list(directory.iterdir())


def test_generation_failure_removes_restrictive_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = fixture_app(tmp_path)
    run_id = _run_with_results(app)
    directory = tmp_path / "exports"
    service = RunExportService(app.extensions["database_factory"], temporary_directory=directory)

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("synthetic export failure")

    monkeypatch.setattr(service, "_write_json", fail)
    with pytest.raises(OSError, match="synthetic"):
        service.create_export(run_id, "json")
    assert list(directory.iterdir()) == []


def test_api_response_close_removes_file_backed_export(tmp_path: Path) -> None:
    app, _, _ = fixture_app(tmp_path)
    run_id = _run_with_results(app)
    directory = tmp_path / "exports"
    app.extensions["run_export_service"] = RunExportService(
        app.extensions["database_factory"], batch_size=1, temporary_directory=directory
    )

    response = app.test_client().get(f"/api/v1/runs/{run_id}/export?format=json", buffered=False)
    assert response.status_code == 200
    assert list(directory.iterdir())
    assert json.loads(response.get_data())["run"]["id"] == str(run_id)
    response.close()
    assert list(directory.iterdir()) == []
