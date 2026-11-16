from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "runs-gui.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "P", "completions_url": "https://p.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id,
        {
            "model_id": "m",
            "display_name": "Model M",
            "pricing": {"input_per_million": "1", "output_per_million": "2"},
        },
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "Set"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "case-one",
            "prompt": "secret prompt",
            "expected_answer": "yes",
            "evaluation_method": "exact_match",
        },
    )
    app.extensions["quality_gate_service"].set_defaults(golden.id, {"min_weighted_score": "0.80"})
    app.config["FIXTURE_IDS"] = (golden.id, model.id)
    return app


def csrf(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match
    return match.group(1)


def create_run(client, app, name="GUI run"):
    golden, model = app.config["FIXTURE_IDS"]
    token = csrf(client, "/runs/new")
    response = client.post(
        "/runs/new",
        data={
            "csrf_token": token,
            "name": name,
            "golden_set_id": str(golden),
            "model_profile_ids": str(model),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return response.headers["Location"].rsplit("/", 1)[-1]


def complete_run(app, run_id: str, *, status="passed"):
    queue = app.extensions["run_queue"]
    lease = queue.claim("gui-test")
    assert lease and str(lease.run_id) == run_id
    queue.complete(
        lease,
        status=status,
        score="1" if status == "passed" else "0",
        weight="1",
        result={
            "passed": status == "passed",
            "candidate": "yes",
            "criteria": [{"id": "correct", "score": "1"}],
        },
        metrics={
            "target_cost": "0.00000100",
            "judge_cost": "99.00000000",
            "target_latency_ms": "100.49",
            "ttft_ms": "20.51",
            "tps": "10.49",
            "input_tokens": 3,
            "output_tokens": 1,
        },
    )
    queue.refresh_run_status(lease.run_id)
    app.extensions["quality_gate_service"].evaluate_run(lease.run_id)


def test_run_can_be_renamed_from_detail_page(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Original run")
    page = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert 'aria-label="Rename run"' in page
    assert f'action="/runs/{run_id}/name"' in page
    token = csrf(client, f"/runs/{run_id}")

    response = client.post(
        f"/runs/{run_id}/name",
        data={"csrf_token": token, "name": "  Renamed & reviewed  "},
        follow_redirects=True,
    )

    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Run renamed." in text
    assert "<h1>Renamed &amp; reviewed</h1>" in text
    assert app.extensions["run_view_service"].run(UUID(run_id))["name"] == "Renamed & reviewed"


def test_run_rename_validates_name_and_csrf_without_mutation(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Stable name")
    token = csrf(client, f"/runs/{run_id}")

    missing_csrf = client.post(f"/runs/{run_id}/name", data={"name": "Changed"})
    assert missing_csrf.status_code == 400
    invalid = client.post(
        f"/runs/{run_id}/name",
        data={"csrf_token": token, "name": "   "},
        follow_redirects=True,
    )

    assert invalid.status_code == 200
    assert "Run name must contain between 1 and 200 characters." in invalid.get_data(as_text=True)
    assert app.extensions["run_view_service"].run(UUID(run_id))["name"] == "Stable name"


def test_new_run_preview_targets_creation_form_and_uses_english(app) -> None:
    text = app.test_client().get("/runs/new").get_data(as_text=True)
    assert 'id="run-create-form"' in text
    assert '<fieldset class="golden-set-picker"><legend>Golden set</legend>' in text
    assert '<fieldset class="run-details-panel"><legend>Run details</legend>' in text
    assert text.index('name="name" form="run-create-form"') < text.index(
        "<legend>Golden set</legend>"
    )
    for heading in ("Run preview", "Model profiles", "Judge", "Quality gates", "Start run"):
        assert f"<legend>{heading}</legend>" in text
    assert 'name="golden_set_id" form="run-create-form" required' in text
    assert "document.getElementById('run-create-form')" in text
    assert "document.querySelector('form')" not in text
    assert "Choose a golden set and models." in text
    assert " en modellen" not in text
    assert "data-sets=" in text and '"active_cases": 1' in text


def test_rerun_opens_editable_prefilled_form_without_starting(app) -> None:
    client = app.test_client()
    golden, model = app.config["FIXTURE_IDS"]
    token = csrf(client, "/runs/new")
    created = client.post(
        "/runs/new",
        data={
            "csrf_token": token,
            "name": "Historical configuration",
            "golden_set_id": str(golden),
            "model_profile_ids": str(model),
            "note": "Copied note",
            "override_gates": "on",
            "min_weighted_score": "0.91",
            "max_total_cost": "1.25",
        },
    )
    run_id = created.headers["Location"].rsplit("/", 1)[-1]

    overview = client.get("/runs").get_data(as_text=True)
    assert f'href="/runs/new?rerun={run_id}"' in overview
    before = len(app.extensions["run_view_service"].list_runs())
    form = client.get(f"/runs/new?rerun={run_id}")
    text = form.get_data(as_text=True)

    assert form.status_code == 200
    assert "<h1>Rerun configuration</h1>" in text
    assert 'value="Rerun of Historical configuration"' in text
    assert f'<option value="{golden}" selected>' in text
    assert f'value="{model}" checked' in text
    assert ">Copied note</textarea>" in text
    assert 'name="override_gates" checked' in text
    assert 'name="min_weighted_score" value="0.91"' in text
    assert 'name="max_total_cost" value="1.25"' in text
    assert "This does not change or automatically start the historical run." in text
    assert len(app.extensions["run_view_service"].list_runs()) == before

    rerun_token = re.search(r'name="csrf_token" value="([^"]+)"', text)
    assert rerun_token
    submitted = client.post(
        "/runs/new",
        data={
            "csrf_token": rerun_token.group(1),
            "name": "Edited rerun",
            "golden_set_id": str(golden),
            "model_profile_ids": str(model),
            "note": "Edited note",
            "override_gates": "on",
            "min_weighted_score": "0.95",
        },
    )
    new_id = submitted.headers["Location"].rsplit("/", 1)[-1]

    assert submitted.status_code == 302
    assert new_id != run_id
    assert app.extensions["run_view_service"].run(UUID(new_id))["name"] == "Edited rerun"
    assert app.extensions["run_view_service"].run(UUID(run_id))["name"] == (
        "Historical configuration"
    )


def test_start_poll_results_filters_and_detail(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app)
    poll = client.get(f"/runs/{run_id}/status").get_json()
    assert poll["progress_percent"] == 0 and not poll["terminal"]
    complete_run(app, run_id)
    poll = client.get(f"/runs/{run_id}/status").get_json()
    assert poll["progress_percent"] == 100 and poll["terminal"]
    page = client.get(
        f"/runs/{run_id}?status=passed&judge_type=exact_match&sort=ttft&direction=desc"
    )
    text = page.get_data(as_text=True)
    assert (
        page.status_code == 200
        and '<div class="run-detail-blocks"><div id="run-status"' in text
        and "Run overview" in text
        and "Model performance" in text
        and "Results by judge type" in text
        and "Model comparison" in text
        and 'aria-label="Score 1.00"' in text
        and "Exact match" in text
        and "bullet-chart" in text
        and "results-table" in text
        and "case-one" in text
        and "0.00000100" in text
        and "99.00000100" not in text
        and "100 ms" in text
        and "21 ms" in text
        and ">10</td>" in text
        and "<td>Model M</td><td>1.00</td><td>0.80</td>" in text
        and 'id="run-results"' in text
        and f'action="/runs/{run_id}#run-results"' in text
        and 'class="filter-grid result-filter-grid"' in text
        and 'name="judge_type"' in text
        and 'value="exact_match" selected' in text
    )
    for key, label in (
        ("rank", "Rank"),
        ("model", "Model"),
        ("score", "Score"),
        ("latency", "Latency p95"),
        ("ttft", "TTFT p95"),
        ("tps", "Mean TPS"),
        ("tokens", "Tokens in/out"),
        ("cost", "Cost"),
        ("gates", "Gates"),
    ):
        assert f"model_sort={key}" in text and f">{label}" in text
    assert '<th aria-sort="ascending"><a class="table-sort"' in text
    assert "model_direction=desc" in text
    assert "status=passed" in text and "judge_type=exact_match" in text
    sorted_page = client.get(f"/runs/{run_id}?model_sort=cost&model_direction=desc")
    assert sorted_page.status_code == 200
    assert '<th aria-sort="descending"><a class="table-sort"' in sorted_page.get_data(as_text=True)
    invalid_sort = client.get(f"/runs/{run_id}?model_sort=invalid")
    invalid_text = invalid_sort.get_data(as_text=True)
    assert "Invalid model comparison sorting." in invalid_text
    assert 'Rank<span aria-hidden="true">▲</span>' in invalid_text
    run_overview = text[text.index("Run overview") : text.index("Model comparison")]
    assert run_overview.count('<article class="kpi">') == 5
    assert "<span>Weighted Score</span><strong>1.00</strong>" in run_overview
    assert "<span>Avg Latency</span><strong>100 ms</strong>" in run_overview
    assert "<span>P95 TTFT</span><strong>21 ms</strong>" in run_overview
    assert "<span>Total Costs</span><strong>0.000</strong><small>1/1 known</small>" in run_overview
    assert "<span>Results</span><strong>1</strong>" in run_overview
    assert "<span>Pass rate</span>" not in run_overview
    assert "<span>Error rate</span>" not in run_overview
    assert "<span>Mean TPS</span>" not in run_overview
    model_table = text[text.index("Model comparison") : text.index("Model performance")]
    assert "<th>Pass rate</th>" not in model_table
    assert "<th>Error rate</th>" not in model_table
    assert '>Latency p95<span class="sr-only">; activate to sort ascending</span>' in model_table
    assert "Latency gem./p95" not in model_table
    assert 'class="gate-dot gate-dot-passed"' in model_table
    assert 'title="Weighted score: Passed"' in model_table
    assert "Cost versus score" in model_table
    assert 'class="scatter-chart"' in model_table
    assert 'class="scatter-point"' in model_table
    assert "Model M: cost 0.000001, score 1.000" in model_table
    assert (
        "total known cost from 0.000000 to 0.000002 on X-axis and weighted score from "
        "0.900 to 1.000 on Y-axis"
    ) in model_table
    assert ">0.900</text>" in model_table
    assert "0.000<br><small>1/1 known</small>" in text
    assert (
        text.index("Run overview")
        < text.index("Model comparison")
        < text.index("Model performance")
    )
    css = client.get("/static/app.css").get_data(as_text=True)
    assert ".run-detail-blocks{display:grid;gap:1rem}" in css
    excluded = client.get(f"/runs/{run_id}?judge_type=llm_judge").get_data(as_text=True)
    assert "No matching results." in excluded
    connection = app.extensions["database_factory"].connect()
    try:
        result_id = connection.execute(
            "SELECT id FROM run_results WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    detail = client.get(f"/runs/{run_id}/results/{result_id}").get_data(as_text=True)
    assert "secret prompt" in detail and "Candidate answer" in detail and "yes" in detail
    assert 'class="evaluation-panel"' in detail
    assert 'class="evaluation-body"' in detail
    assert "<dt>Outcome</dt>" in detail and ">Passed</span>" in detail
    assert "<h3>Criteria</h3>" in detail and "Correct" in detail
    assert "Raw evaluation JSON" in detail
    assert "api_key_secret_id" not in detail

    overview = client.get("/runs").get_data(as_text=True)
    assert "<th>Score</th>" in overview
    assert "1.0000" in overview
    assert 'id="run-chart-metric"' not in overview

    assert "<h3>Model metrics</h3>" in text
    assert "Compare models in this run using one metric." in text
    assert 'id="run-chart-metric"' in text
    for value, label in (
        ("score", "Score"),
        ("cost", "Cost"),
        ("latency", "Latency"),
        ("tokens", "Total tokens"),
    ):
        assert f'<option value="{value}">{label}</option>' in text
        assert f'data-run-chart="{value}"' in text
    assert "Score by model" in text
    assert "Model M: 1.0000" in text
    assert "Model M: 0.000001" in text
    assert "Model M: 100 ms" in text
    assert "Model M: 4 tokens" in text
    assert "Y-axis: Weighted score. X-axis: Model." in text
    assert "chart.hidden=chart.dataset.runChart!==chartMetric.value" in text
    assert (
        text.index("Cost versus score")
        < text.index("Model metrics")
        < text.index("Model performance")
    )


def test_guardrail_is_distinct_in_ui_aggregates_and_export(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Guardrail run")
    queue = app.extensions["run_queue"]
    lease = queue.claim("guardrail-worker")
    assert lease is not None
    queue.fail(
        lease,
        "provider_guardrail",
        {
            "outcome_category": "provider_guardrail",
            "outcome_stage": "target",
            "outcome_code": "provider_guardrail",
            "provider_evidence": {
                "finish_reason": "content_filter",
                "provider_request_id": "safe-request-id",
            },
            "candidate": "partial output",
            "partial_candidate": True,
        },
        metrics={"target_latency_ms": "25"},
    )
    queue.refresh_run_status(run_id)

    run_page = client.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "1 guardrail · 0 other error" in run_page
    assert "Provider guardrail" in run_page

    connection = app.extensions["database_factory"].connect()
    try:
        result_id = connection.execute(
            "SELECT id FROM run_results WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    detail = client.get(f"/runs/{run_id}/results/{result_id}").get_data(as_text=True)
    assert "Provider guardrail intervention" in detail
    assert "blocked by provider policy" in detail
    assert "Partial candidate answer" in detail and "partial output" in detail
    assert "safe-request-id" in detail and "content_filter" in detail

    aggregates = client.get(f"/api/v1/runs/{run_id}/aggregates").get_json()["models"][0]
    assert aggregates["errors"] == 1 and aggregates["guardrails"] == 1
    assert aggregates["error_rate"] == "1" and aggregates["guardrail_rate"] == "1"
    exported = client.get(f"/api/v1/runs/{run_id}/export?format=json").get_json()
    assert exported["results"][0]["outcome_category"] == "provider_guardrail"
    assert exported["results"][0]["provider_evidence_json"] == (
        '{"finish_reason":"content_filter","provider_request_id":"safe-request-id"}'
    )


def test_completed_runs_compare_filter_link_and_export(app) -> None:
    client = app.test_client()
    baseline_id = create_run(client, app, "Baseline")
    complete_run(app, baseline_id, status="passed")
    candidate_id = create_run(client, app, "Candidate")
    complete_run(app, candidate_id, status="failed")

    chooser = client.get(f"/runs/{candidate_id}/compare")
    chooser_text = chooser.get_data(as_text=True)
    assert chooser.status_code == 200
    assert "Baseline" in chooser_text and "Compatible" in chooser_text
    page = client.get(f"/runs/{candidate_id}/compare?baseline={baseline_id}&filter=regression")
    text = page.get_data(as_text=True)
    assert "Regressions</span><strong>1" in text
    assert "case-one" in text and "Model M" in text
    assert f"/runs/{baseline_id}/results/" in text
    assert f"/runs/{candidate_id}/results/" in text
    export = client.get(f"/runs/{candidate_id}/compare/{baseline_id}/export")
    payload = json.loads(export.data)
    assert export.status_code == 200
    assert payload["compatibility"] == "compatible"
    assert payload["summary"]["regressions"] == 1
    api = client.get(f"/api/v1/runs/{candidate_id}/compare/{baseline_id}")
    assert api.status_code == 200 and api.get_json()["summary"]["regressions"] == 1
    assert client.get(f"/runs/{candidate_id}/compare?baseline=bad").status_code == 400
    assert (
        client.get(f"/api/v1/runs/{candidate_id}/compare/{baseline_id}?filter=bad").status_code
        == 400
    )


def test_cancel_delete_and_export_require_csrf_and_confirmation(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Cancel me")
    assert client.post(f"/runs/{run_id}/cancel").status_code == 400
    token = csrf(client, f"/runs/{run_id}")
    no_confirm = client.post(
        f"/runs/{run_id}/cancel", data={"csrf_token": token}, follow_redirects=True
    )
    assert "Explicitly confirm cancellation" in no_confirm.get_data(as_text=True)
    client.post(f"/runs/{run_id}/cancel", data={"csrf_token": token, "confirm": "on"})
    export = client.get(f"/runs/{run_id}/export")
    assert "sensitive prompts" in export.get_data(as_text=True)
    token = csrf(client, f"/runs/{run_id}/export")
    denied = client.post(f"/runs/{run_id}/export", data={"csrf_token": token, "format": "json"})
    assert "confirm the warning" in denied.get_data(as_text=True)
    downloaded = client.post(
        f"/runs/{run_id}/export",
        data={"csrf_token": token, "format": "json", "confirm_sensitive": "on"},
    )
    assert (
        downloaded.status_code == 200 and json.loads(downloaded.data)["run"]["name"] == "Cancel me"
    )
    token = csrf(client, f"/runs/{run_id}")
    client.post(f"/runs/{run_id}/delete", data={"csrf_token": token, "confirm": "on"})
    assert client.get(f"/runs/{run_id}").status_code == 404


def test_bulk_delete_runs_with_select_all_control(app) -> None:
    client = app.test_client()
    first = create_run(client, app, "Delete first")
    second = create_run(client, app, "Delete second")
    retained = create_run(client, app, "Keep this run")
    for run_id in (first, second, retained):
        complete_run(app, run_id)

    page = client.get("/runs")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'id="select-all-runs"' in text
    assert "Select all runs" in text
    assert text.count('class="run-select"') == 3
    assert "Delete selected runs" in text
    assert "Permanently delete selected runs?" in text

    token = csrf(client, "/runs")
    empty = client.post("/runs/delete", data={"csrf_token": token}, follow_redirects=True)
    assert "Select one or more valid, completed runs." in empty.get_data(as_text=True)

    token = csrf(client, "/runs")
    deleted = client.post(
        "/runs/delete",
        data={"csrf_token": token, "run_ids": [first, second]},
        follow_redirects=True,
    )
    deleted_text = deleted.get_data(as_text=True)
    assert "2 runs permanently deleted." in deleted_text
    assert "Delete first" not in deleted_text
    assert "Delete second" not in deleted_text
    assert "Keep this run" in deleted_text
    assert client.get(f"/runs/{first}").status_code == 404
    assert client.get(f"/runs/{second}").status_code == 404
    assert client.get(f"/runs/{retained}").status_code == 200


def test_rerun_failed_jobs_from_gui(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Failing")
    complete_run(app, run_id, status="error")
    token = csrf(client, f"/runs/{run_id}")
    response = client.post(
        f"/runs/{run_id}/rerun-failures",
        data={"csrf_token": token, "name": "Retry"},
        follow_redirects=False,
    )
    assert response.status_code == 302 and response.headers["Location"] != f"/runs/{run_id}"
