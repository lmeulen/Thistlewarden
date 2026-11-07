from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest

from evaluator import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(
        env_file=None,
        settings_environment={
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
    token = csrf(client, "/runs/nieuw")
    response = client.post(
        "/runs/nieuw",
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
        and 'class="filter-grid result-filter-grid"' in text
        and 'name="judge_type"' in text
        and 'value="exact_match" selected' in text
    )
    assert "<th>Cost</th><th>Gates</th>" in text
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
    assert "<th>Latency p95</th>" in model_table
    assert "Latency gem./p95" not in model_table
    assert 'class="gate-dot gate-dot-passed"' in model_table
    assert 'title="Weighted score: Passed"' in model_table
    assert "Cost versus score" in model_table
    assert 'class="scatter-chart"' in model_table
    assert 'class="scatter-point"' in model_table
    assert "Model M: cost 0.000, score 1.00" in model_table
    assert "weighted score from 0.90 to 1.00 on Y-axis" in model_table
    assert ">0.90</text>" in model_table
    assert "0.000<br><small>1/1 known</small>" in text
    assert (
        text.index("Run overview")
        < text.index("Model comparison")
        < text.index("Model performance")
    )
    excluded = client.get(f"/runs/{run_id}?judge_type=llm_judge").get_data(as_text=True)
    assert "No matching results." in excluded
    connection = app.extensions["database_factory"].connect()
    try:
        result_id = connection.execute(
            "SELECT id FROM run_results WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    detail = client.get(f"/runs/{run_id}/resultaten/{result_id}").get_data(as_text=True)
    assert "secret prompt" in detail and "Candidate answer" in detail and "yes" in detail
    assert "api_key_secret_id" not in detail

    overview = client.get("/runs").get_data(as_text=True)
    assert "<th>Score</th>" in overview
    assert "1.0000" in overview


def test_cancel_delete_and_export_require_csrf_and_confirmation(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Cancel me")
    assert client.post(f"/runs/{run_id}/annuleren").status_code == 400
    token = csrf(client, f"/runs/{run_id}")
    no_confirm = client.post(
        f"/runs/{run_id}/annuleren", data={"csrf_token": token}, follow_redirects=True
    )
    assert "Explicitly confirm cancellation" in no_confirm.get_data(as_text=True)
    client.post(f"/runs/{run_id}/annuleren", data={"csrf_token": token, "confirm": "on"})
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
    client.post(f"/runs/{run_id}/verwijderen", data={"csrf_token": token, "confirm": "on"})
    assert client.get(f"/runs/{run_id}").status_code == 404


def test_rerun_failed_jobs_from_gui(app) -> None:
    client = app.test_client()
    run_id = create_run(client, app, "Failing")
    complete_run(app, run_id, status="error")
    token = csrf(client, f"/runs/{run_id}")
    response = client.post(
        f"/runs/{run_id}/mislukte-opnieuw",
        data={"csrf_token": token, "name": "Retry"},
        follow_redirects=False,
    )
    assert response.status_code == 302 and response.headers["Location"] != f"/runs/{run_id}"
