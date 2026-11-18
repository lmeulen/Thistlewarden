from __future__ import annotations

import base64
import os
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app
from evaluator.onboarding import OnboardingService
from evaluator.time import to_utc_iso, utc_now

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "onboarding.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as session:
        return str(session["csrf_token"])


def test_empty_dashboard_shows_ordered_accessible_pilot_steps(app) -> None:
    page = app.test_client().get("/").get_data(as_text=True)

    assert "Run your first pilot evaluation" in page
    assert "0 of 6 steps complete" in page
    assert '<ol class="onboarding-steps">' in page
    for title in (
        "Add a provider endpoint",
        "Test endpoint connectivity and a model profile",
        "Create or import a small golden set",
        "Preview one deterministic evaluator",
        "Start a one-model pilot run",
        "Inspect the first completed result",
    ):
        assert title in page
    assert page.count("Status:</strong> Not complete") == 6
    assert 'aria-label="Pilot guidance actions"' in page
    assert ">Mark as done</button>" in page
    assert "does not complete setup, bypass blockers, create records" in page
    assert "No credential, endpoint, provider call, paid call, or judge call" in page
    assert 'href="/manual/getting-started"' in page


def test_dismiss_and_restore_persist_across_clients(app) -> None:
    first = app.test_client()
    dismissed = first.post("/onboarding/dismiss", data={"csrf_token": _csrf(first)})
    assert dismissed.status_code == 302

    second = app.test_client()
    page = second.get("/").get_data(as_text=True)
    assert "Run your first pilot evaluation" not in page
    assert "Pilot guidance hidden" in page

    restored = second.post("/onboarding/restore", data={"csrf_token": _csrf(second)})
    assert restored.status_code == 302
    assert "Run your first pilot evaluation" in second.get("/").get_data(as_text=True)


def test_mark_as_done_persists_is_reversible_and_does_not_change_progress(app) -> None:
    first = app.test_client()
    response = first.post("/onboarding/complete", data={"csrf_token": _csrf(first)})
    assert response.status_code == 302

    second = app.test_client()
    page = second.get("/").get_data(as_text=True)
    assert "Run your first pilot evaluation" not in page
    assert "Pilot guidance marked as done" in page
    assert "did not change setup or evaluation status" in page
    assert "Reopen pilot guidance" in page

    factory = app.extensions["database_factory"]
    connection = factory.connect()
    try:
        preference = connection.execute(
            "SELECT dismissed,manually_completed FROM onboarding_preferences WHERE id=1"
        ).fetchone()
        assert tuple(preference) == (0, 1)
        for table in (
            "endpoints",
            "model_profiles",
            "golden_sets",
            "golden_set_items",
            "runs",
            "onboarding_events",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()

    state = OnboardingService(factory).state()
    assert state.manually_completed is True
    assert state.complete is False
    assert state.completed_count == 0

    reopened = second.post("/onboarding/restore", data={"csrf_token": _csrf(second)})
    assert reopened.status_code == 302
    restored_page = second.get("/").get_data(as_text=True)
    assert "Run your first pilot evaluation" in restored_page
    assert "0 of 6 steps complete" in restored_page
    assert "Pilot guidance marked as done" not in restored_page


def test_explicit_starter_set_is_synthetic_deterministic_editable_and_removable(app) -> None:
    client = app.test_client()
    response = client.post("/onboarding/starter-set", data={"csrf_token": _csrf(client)})

    assert response.status_code == 302
    assert "/golden-sets/" in response.headers["Location"]
    factory = app.extensions["database_factory"]
    connection = factory.connect()
    try:
        golden_set = connection.execute(
            "SELECT id,name,description,tags_json FROM golden_sets"
        ).fetchone()
        assert golden_set is not None
        assert golden_set["name"] == "Synthetic pilot examples"
        assert "does not establish model quality" in golden_set["description"]
        assert '"synthetic"' in golden_set["tags_json"]
        items = connection.execute(
            "SELECT evaluation_method,tags_json FROM golden_set_items ORDER BY sort_order"
        ).fetchall()
        assert [item["evaluation_method"] for item in items] == [
            "contains",
            "exact_match",
            "json_schema",
        ]
        assert all('"synthetic"' in item["tags_json"] for item in items)
    finally:
        connection.close()

    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Edit golden set" in page
    service = app.extensions["golden_set_service"]
    assert service.delete_set(UUID(golden_set["id"])) is True


def test_partial_success_actions_are_record_derived_and_survive_restart_clients(app) -> None:
    factory = app.extensions["database_factory"]
    now = to_utc_iso(utc_now())
    endpoint_id = "00000000-0000-0000-0000-000000000037"
    model_id = UUID("00000000-0000-0000-0000-000000000038")
    connection = factory.connect()
    try:
        connection.execute(
            """INSERT INTO endpoints(
                id,name,completions_url,api_key_header,api_key_prefix,timeout_seconds,max_retries,
                active,trusted_internal_http,security_warning_confirmed,created_at,updated_at,api_format
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                endpoint_id,
                "Pilot endpoint",
                "https://example.test/v1/chat/completions",
                "Authorization",
                "Bearer ",
                60,
                0,
                1,
                0,
                0,
                now,
                now,
                "openai",
            ),
        )
        connection.execute(
            """INSERT INTO model_profiles VALUES(
                ?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,0,'{}','0','0',NULL,NULL,NULL,NULL,
                NULL,NULL,NULL,NULL,1,?,?, 'max_tokens'
            )""",
            (str(model_id), endpoint_id, "pilot-model", "Pilot model", now, now),
        )
        connection.commit()
    finally:
        connection.close()

    app.extensions["onboarding_service"].record_success("model_test_succeeded", model_id)
    page = app.test_client().get("/").get_data(as_text=True)
    assert "2 of 6 steps complete" in page
    endpoint_step = page.split("Add a provider endpoint", 1)[1].split("</li>", 1)[0]
    model_step = page.split("Test endpoint connectivity", 1)[1].split("</li>", 1)[0]
    assert "Status:</strong> Complete" in endpoint_step
    assert "Status:</strong> Complete" in model_step


def test_existing_completed_workflow_is_not_forced_through_onboarding(app) -> None:
    factory = app.extensions["database_factory"]
    now = to_utc_iso(utc_now())
    connection = factory.connect()
    try:
        connection.execute(
            """INSERT INTO runs(id,name,golden_set_id,status,created_at,completed_at,
                recovery_warning,gate_status) VALUES(?,?,?,?,?,?,0,'not_evaluated')""",
            ("run-37", "Existing run", "set-37", "completed", now, now),
        )
        connection.execute(
            "INSERT INTO run_case_snapshots VALUES(?,?,?,?,?,?)",
            ("case-37", "run-37", "source-case-37", "case", "1", "{}"),
        )
        connection.execute(
            "INSERT INTO run_model_snapshots VALUES(?,?,?,?,?)",
            ("model-37", "run-37", "source-model-37", "endpoint-37", "{}"),
        )
        connection.execute(
            """INSERT INTO run_jobs(id,run_id,case_snapshot_id,model_snapshot_id,status,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
            ("job-37", "run-37", "case-37", "model-37", "completed", now, now),
        )
        connection.execute(
            """INSERT INTO run_results(id,job_id,run_id,model_snapshot_id,case_snapshot_id,
                status,score,weight,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "result-37",
                "job-37",
                "run-37",
                "model-37",
                "case-37",
                "passed",
                "1",
                "1",
                "{}",
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    page = app.test_client().get("/").get_data(as_text=True)
    assert "Run your first pilot evaluation" not in page
    assert "Pilot guidance hidden" not in page
