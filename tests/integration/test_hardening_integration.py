from __future__ import annotations

import base64
import multiprocessing
import os
import re
import time
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.runtime import InstanceLock

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "hardening.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
            "MAX_IMPORT_FILE_BYTES": "1024",
        },
        initialize_database=True,
        test_config={"TESTING": True},
    )


def _hold_lock(
    path: str, ready: multiprocessing.Queue[bool], stop: multiprocessing.Queue[bool]
) -> None:
    with InstanceLock(Path(path)):
        ready.put(True)
        stop.get(timeout=10)


def test_real_second_process_cannot_acquire_instance_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    stop = context.Queue()
    path = str(tmp_path / "instance.lock")
    process = context.Process(target=_hold_lock, args=(path, ready, stop))
    process.start()
    try:
        assert ready.get(timeout=10)
        from evaluator.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            InstanceLock(Path(path)).acquire()
    finally:
        stop.put(True)
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
    assert process.exitcode == 0


def test_security_headers_host_origin_upload_limit_and_readiness(app) -> None:
    client = app.test_client()
    response = client.get("/", headers={"Host": "localhost:5000"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Frame-Options"] == "DENY"
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp)
    assert nonce and f'nonce="{nonce.group(1)}"' in response.get_data(as_text=True)
    assert client.get("/", headers={"Host": "attacker.test"}).status_code == 400
    assert (
        client.post(
            "/api/v1/settings", headers={"Origin": "https://attacker.test"}, json={}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/settings", headers={"Origin": "null"}, json={}).status_code == 403
    oversized = client.post(
        "/api/v1/golden-sets/import?format=json",
        data=b"x" * (1024 + 64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert client.get("/health").status_code == 200
    assert client.get("/ready").get_json() == {"status": "ready"}


def test_cancel_fences_running_worker_final_commit(app) -> None:
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "P", "completions_url": "https://example.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "m", "display_name": "M"}
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "G"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {"case_id": "c", "prompt": "x", "expected_answer": "x", "evaluation_method": "exact_match"},
    )
    queue = app.extensions["run_queue"]
    run_id = queue.create_run(
        {"name": "R", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    lease = queue.claim("worker")
    assert lease
    queue.cancel(run_id)
    from evaluator.runs import LeaseLost

    with pytest.raises(LeaseLost):
        queue.complete(lease, status="passed", score="1", weight="1", result={})
    connection = app.extensions["database_factory"].connect()
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM run_results WHERE run_id=?", (str(run_id),)
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_ten_thousand_results_page_and_aggregate_budget(app) -> None:
    factory = app.extensions["database_factory"]
    connection = factory.connect()
    now = "2026-01-01T00:00:00.000000Z"
    run_id = "10000000-0000-0000-0000-000000000001"
    model_id = "20000000-0000-0000-0000-000000000001"
    model_json = '{"display_name":"M","model_id":"m"}'
    case_json = '{"case_id":"c","prompt":"p","system_prompt":null,"expected_answer":"x"}'
    try:
        connection.execute(
            """INSERT INTO runs(
                id,name,golden_set_id,status,created_at,recovery_warning,
                quality_gates_snapshot_json,gate_status
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (run_id, "Perf", "g", "completed", now, 0, "{}", "not_evaluated"),
        )
        connection.execute(
            "INSERT INTO run_model_snapshots VALUES(?,?,?,?,?)",
            (model_id, run_id, "source", "endpoint", model_json),
        )
        cases = [
            (f"3{i:031d}", run_id, f"source-{i}", f"c-{i}", "1", case_json) for i in range(10000)
        ]
        connection.executemany("INSERT INTO run_case_snapshots VALUES(?,?,?,?,?,?)", cases)
        jobs = [
            (
                f"5{i:031d}",
                run_id,
                f"3{i:031d}",
                model_id,
                "completed",
                1,
                0,
                now,
                now,
            )
            for i in range(10000)
        ]
        connection.executemany(
            """INSERT INTO run_jobs(
                id,run_id,case_snapshot_id,model_snapshot_id,status,attempt_count,
                lease_generation,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            jobs,
        )
        rows = [
            (
                f"4{i:031d}",
                f"5{i:031d}",
                run_id,
                model_id,
                f"3{i:031d}",
                "passed",
                "1",
                "1",
                '{"passed":true}',
                "0",
                "0",
                "10",
                "2",
                "5",
                1,
                1,
                0,
                0,
                now,
            )
            for i in range(10000)
        ]
        connection.executemany(
            "INSERT INTO run_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
    finally:
        connection.close()
    started = time.perf_counter()
    page = app.extensions["run_view_service"].results(
        __import__("uuid").UUID(run_id),
        __import__("evaluator.run_views", fromlist=["ResultQuery"]).ResultQuery(),
    )
    page_seconds = time.perf_counter() - started
    started = time.perf_counter()
    aggregate = app.extensions["aggregate_service"].for_run(__import__("uuid").UUID(run_id))
    aggregate_seconds = time.perf_counter() - started
    assert len(page) == 101 and aggregate[0]["total"] == 10000
    assert page_seconds < 2.0 and aggregate_seconds < 3.0
