"""Offline release acceptance against running Compose stack."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:5000")
FIXTURE_SECRET = "release-fixture-secret"


def _validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("E2E_BASE_URL must use HTTP(S) and a loopback host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("E2E_BASE_URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def request(path: str, *, method: str = "GET", body: Any | None = None) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    response = urllib.request.urlopen(  # noqa: S310 -- validated HTTP(S) loopback URL
        urllib.request.Request(
            _validate_base_url(BASE) + path,
            data=data,
            headers=headers,
            method=method,
        ),
        timeout=10,
    )
    return response.status, response.read()


def wait_ready() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status, content = request("/ready")
            if status == 200 and json.loads(content)["status"] == "ready":
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise RuntimeError("evaluator did not become ready")


def main() -> None:
    wait_ready()
    bundle = json.loads(Path("examples/release-configuration.json").read_text(encoding="utf-8"))
    status, preview = request(
        "/api/v1/configuration/import?mode=merge&preview=true", method="POST", body=bundle
    )
    assert status == 200 and json.loads(preview)["creates"] == 11
    status, committed = request(
        "/api/v1/configuration/import?mode=merge&preview=false", method="POST", body=bundle
    )
    assert status == 200 and json.loads(committed)["creates"] == 11
    status, patched = request(
        "/api/v1/endpoints/10000000-0000-4000-8000-000000000001",
        method="PATCH",
        body={"api_key": FIXTURE_SECRET},
    )
    assert status == 200 and FIXTURE_SECRET.encode() not in patched
    status, exported = request("/api/v1/configuration/export")
    assert status == 200 and FIXTURE_SECRET.encode() not in exported
    status, created = request(
        "/api/v1/runs",
        method="POST",
        body={
            "name": "Offline release E2E",
            "golden_set_id": "40000000-0000-4000-8000-000000000001",
            "model_profile_ids": [
                "20000000-0000-4000-8000-000000000001",
                "20000000-0000-4000-8000-000000000002",
            ],
            "judge_profile_id": "30000000-0000-4000-8000-000000000001",
        },
    )
    run = json.loads(created)["run"]
    run_id = run["id"]
    deadline = time.monotonic() + 60
    while run["status"] in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(0.5)
        run = json.loads(request(f"/api/v1/runs/{run_id}")[1])["run"]
    assert run["status"] == "completed", run
    aggregates = json.loads(request(f"/api/v1/runs/{run_id}/aggregates")[1])["models"]
    assert len(aggregates) == 2 and all(item["pass_rate"] == "1" for item in aggregates)
    gates = json.loads(request(f"/api/v1/runs/{run_id}/quality-gates")[1])
    assert gates["status"] == "passed"
    for format_ in ("json", "csv", "html"):
        status, export = request(f"/api/v1/runs/{run_id}/export?format={format_}")
        assert status == 200 and export and FIXTURE_SECRET.encode() not in export
    print(json.dumps({"status": "passed", "run_id": run_id, "models": len(aggregates)}))


if __name__ == "__main__":
    main()
