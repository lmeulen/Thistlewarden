from __future__ import annotations

import base64
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest

from evaluator import create_app
from evaluator.config_io import ConfigurationExchangeService
from evaluator.endpoints import SQLiteEndpointRepository
from evaluator.mock_provider import Handler
from evaluator.provider import OpenAIProviderClient, ProviderHeaderResolver
from evaluator.streaming import OpenAIStreamingClient
from evaluator.worker_runtime import JudgeCaller, StreamingTargetCaller
from evaluator.workers import WorkerPipeline

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]


def test_offline_release_flow_all_evaluators_two_models_and_exports(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = create_app(
        env_file=None,
        settings_environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "e2e.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
            "RUN_CONCURRENCY": "2",
        },
        initialize_database=True,
    )
    try:
        bundle = json.loads(
            (ROOT / "examples/release-configuration.json").read_text(encoding="utf-8")
        )
        endpoint = bundle["endpoints"][0]["data"]
        endpoint["completions_url"] = f"http://localhost:{server.server_port}/v1/chat/completions"
        endpoint["models_url"] = f"http://localhost:{server.server_port}/v1/models"
        endpoint["trusted_internal_http"] = False
        endpoint["security_warning_confirmed"] = False
        exchange = ConfigurationExchangeService(app.extensions["database_factory"])
        exchange.import_bundle(bundle, "merge", preview=False)
        queue = app.extensions["run_queue"]
        run_id = queue.create_run(
            {
                "name": "Native offline E2E",
                "golden_set_id": "40000000-0000-4000-8000-000000000001",
                "model_profile_ids": [
                    "20000000-0000-4000-8000-000000000001",
                    "20000000-0000-4000-8000-000000000002",
                ],
                "judge_profile_id": "30000000-0000-4000-8000-000000000001",
            }
        )
        endpoint_repository = SQLiteEndpointRepository(app.extensions["database_factory"])
        headers = ProviderHeaderResolver(
            endpoint_repository, app.extensions["secret_repository"], {}
        )
        executor = app.extensions["provider_call_executor"]
        target = StreamingTargetCaller(
            endpoint_repository,
            OpenAIStreamingClient(headers, max_response_bytes=1_000_000),
            executor,
        )
        judge = JudgeCaller(
            queue,
            endpoint_repository,
            OpenAIProviderClient(headers, max_response_bytes=1_000_000),
            executor,
        )
        probe_connection = app.extensions["database_factory"].connect()
        try:
            probe = probe_connection.execute(
                "SELECT case_snapshot_id,model_snapshot_id FROM run_jobs WHERE run_id=? LIMIT 1",
                (str(run_id),),
            ).fetchone()
        finally:
            probe_connection.close()
        probe_outcome = target(
            queue.snapshot(UUID(probe["case_snapshot_id"]), "case"),
            queue.snapshot(UUID(probe["model_snapshot_id"]), "model"),
        )
        assert probe_outcome.evaluate, probe_outcome
        pipeline = WorkerPipeline(
            queue,
            target,
            judge=judge,
            gates=app.extensions["quality_gate_service"],
            heartbeat_interval=0.05,
        )
        while pipeline.run_once("release-e2e"):
            pass
        run = queue.get_run(run_id)
        connection = app.extensions["database_factory"].connect()
        try:
            diagnostics = [
                dict(row)
                for row in connection.execute(
                    "SELECT j.status,j.error_code,a.metadata_json FROM run_jobs j "
                    "LEFT JOIN run_attempts a ON a.job_id=j.id WHERE j.run_id=?",
                    (str(run_id),),
                )
            ]
        finally:
            connection.close()
        assert run["status"] == "completed", diagnostics
        aggregates = app.extensions["aggregate_service"].for_run(run_id)
        assert len(aggregates) == 2
        assert all(item["pass_rate"] == 1 for item in aggregates)
        assert all(item["target_cost"] is not None for item in aggregates)
        gates = app.extensions["quality_gate_service"].results(run_id)
        assert gates["status"] == "passed"
        exports = app.extensions["run_export_service"]
        assert exports.export_json(run_id)
        assert exports.export_csv(run_id)
        html = exports.export_html(run_id)
        assert b"<html" in html and b"release-fixture-secret" not in html
        connection = app.extensions["database_factory"].connect()
        try:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM run_results WHERE run_id=?", (str(run_id),)
                ).fetchone()[0]
                == 12
            )
            assert UUID(run["id"]) == run_id
        finally:
            connection.close()
    finally:
        app.extensions["shutdown_services"]()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
