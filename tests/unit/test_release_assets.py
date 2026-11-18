from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluator.config import load_bootstrap_settings
from evaluator.config_io import ConfigurationBundle, ConfigurationExchangeService
from evaluator.database import SQLiteConnectionFactory
from evaluator.errors import ConfigurationError
from evaluator.migrations import MigrationManager

ROOT = Path(__file__).parents[2]


def test_product_branding_is_consistent() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    compose_e2e = (ROOT / "compose.e2e.yaml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'name = "thistlewarden"' in pyproject
    assert 'version = "0.2.0"' in pyproject
    assert 'thistlewarden = "evaluator.server:main"' in pyproject
    assert 'thistlewarden-admin = "evaluator.cli:main"' in pyproject
    assert "name: thistlewarden" in compose
    assert "image: thistlewarden:0.2.0" in compose
    assert "image: thistlewarden:0.2.0" in compose_e2e
    assert "name: thistlewarden-data" in compose
    assert readme.startswith("# Thistlewarden\n")
    assert "**Version 0.2.0 is a beta release.**" in readme
    assert "docs/release-notes-0.2.0.md" in readme

    searched_roots = [ROOT / "src", ROOT / "docs", ROOT / "tests"]
    searched_files = [ROOT / "README.md", ROOT / "SECURITY.md"]
    searched_files.extend(
        path
        for root in searched_roots
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and "archive" not in path.parts
    )
    for path in searched_files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert "LLMEvaluator" + " Evaluator" not in text, path


def test_generated_and_sensitive_directories_are_excluded_from_release_contexts() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert {"build/", "dist/", "data/", "data2/", "exports/", "testsets/"} <= set(gitignore)
    assert {
        "build",
        "dist",
        "data",
        "data2",
        "exports",
        "testsets",
        "docs/archive",
        ".coverage",
        "htmlcov",
    } <= set(dockerignore)


def test_quality_gate_table_separates_model_groups() -> None:
    template = (ROOT / "src/evaluator/templates/run_detail.html").read_text(encoding="utf-8")
    css = (ROOT / "src/evaluator/static/app.css").read_text(encoding="utf-8")

    assert 'class="quality-gates-table"' in template
    assert "gate.model_snapshot_id != loop.previtem.model_snapshot_id" in template
    assert 'class="gate-model-separator"' in template
    assert ".quality-gates-table tr.gate-model-separator>td{border-top:3px solid #526573}" in css


def test_release_configuration_validates_against_application_schema() -> None:
    payload = json.loads((ROOT / "examples/release-configuration.json").read_text(encoding="utf-8"))
    bundle = ConfigurationBundle.model_validate(payload)
    assert len(bundle.model_profiles) == 2
    assert len(bundle.golden_set_items) == 6
    serialized = bundle.model_dump_json()
    assert "api_key" not in serialized or '"api_key":null' in serialized
    assert "release-fixture-secret" not in serialized


def test_release_configuration_passes_real_import_validation(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "release.db")
    MigrationManager(factory).migrate()
    service = ConfigurationExchangeService(factory)
    payload = json.loads((ROOT / "examples/release-configuration.json").read_text())
    plan = service.import_bundle(payload, "merge", preview=True)
    assert plan.creates == 11 and plan.missing_secrets == []
    service.import_bundle(payload, "merge", preview=False)
    assert len(service.export().golden_set_items) == 6


def test_container_bind_requires_explicit_container_mode() -> None:
    common = {
        "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
        "HOST": "0.0.0.0",
        "DATABASE_PATH": "/tmp/evaluator.db",
    }
    with pytest.raises(ConfigurationError):
        load_bootstrap_settings(env_file=None, environment=common)
    settings = load_bootstrap_settings(
        env_file=None, environment={**common, "CONTAINER_MODE": "true"}
    )
    assert settings.host == "0.0.0.0" and settings.container_mode


def test_dockerfile_is_single_nonroot_runtime_with_healthcheck() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12.10-slim-bookworm" in text
    assert "USER evaluator:evaluator" in text
    assert 'ENTRYPOINT ["python", "-m", "evaluator.container_entrypoint"]' in text
    assert "HEALTHCHECK" in text and "/ready" in text
    assert "gunicorn" not in text


def test_compose_is_local_single_replica_named_volume() -> None:
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${PORT:-5000}:5000"' in text
    assert "evaluator-data:/var/lib/thistlewarden" in text
    assert "replicas:" not in text and "container_name:" not in text
    assert "../" not in text
    assert 'APP_PROCESS_COUNT: "1"' in text
    assert "cap_drop:" in text and "no-new-privileges:true" in text


def test_runtime_and_requirement_dependencies_stay_synchronized() -> None:
    requirements = {
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = {
        line.strip().strip('",')
        for line in pyproject.split("dependencies = [", 1)[1].split("]", 1)[0].splitlines()
        if "==" in line
    }
    assert runtime <= requirements
