from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.cli import main

pytestmark = pytest.mark.integration


def _environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    database = tmp_path / "cli.db"
    environment = {
        "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
        "DATABASE_PATH": str(database),
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "LOG_LEVEL": "WARNING",
    }
    return database, environment


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    _, environment = _environment(tmp_path)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "CLI endpoint", "completions_url": "https://cli.test/v1/chat/completions"}
    )
    model = app.extensions["model_profile_service"].create(
        endpoint.id,
        {
            "model_id": "cli-model",
            "display_name": "CLI model",
            "pricing": {"input_per_million": "1", "output_per_million": "2"},
        },
    )
    golden = app.extensions["golden_set_service"].create_set({"name": "CLI set"})
    app.extensions["golden_set_service"].create_item(
        golden.id,
        {
            "case_id": "case-1",
            "prompt": "sensitive prompt",
            "expected_answer": "sensitive answer",
            "evaluation_method": "exact_match",
        },
    )
    app.extensions["shutdown_services"]()
    return str(golden.id), str(model.id)


def test_cli_lookup_start_show_and_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    golden_id, model_id = _seed(tmp_path, monkeypatch)

    assert (
        main(
            [
                "--env-file",
                str(tmp_path / "missing.env"),
                "run",
                "lookup",
                "models",
                "--output",
                "json",
            ]
        )
        == 0
    )
    lookup = capsys.readouterr().out
    assert model_id in lookup
    assert "CLI model" in lookup
    assert "sensitive" not in lookup

    result = main(
        [
            "--env-file",
            str(tmp_path / "missing.env"),
            "run",
            "start",
            "--name",
            "Automation run",
            "--golden-set",
            golden_id,
            "--model",
            model_id,
            "--no-gates",
            "--output",
            "json",
        ]
    )
    assert result == 0
    output = capsys.readouterr().out
    assert '"status":"pending"' in output
    assert "sensitive prompt" not in output
    run_id = json.loads(output)["run_id"]

    prefix = ["--env-file", str(tmp_path / "missing.env")]
    assert main([*prefix, "run", "show", run_id, "--output", "json"]) == 0
    assert f'"run_id":"{run_id}"' in capsys.readouterr().out

    destination = tmp_path / "run.json"
    assert main([*prefix, "run", "export", run_id, "--json", str(destination)]) == 0
    assert destination.exists()
    assert "sensitive prompt" in destination.read_text(encoding="utf-8")
    capsys.readouterr()

    assert main([*prefix, "run", "export", run_id, "--json", str(destination)]) == 9
    assert "exists" in capsys.readouterr().err
    assert (
        main(
            [
                *prefix,
                "run",
                "export",
                run_id,
                "--json",
                str(destination),
                "--overwrite",
            ]
        )
        == 0
    )


def test_cli_name_selection_and_invalid_or_ambiguous_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed(tmp_path, monkeypatch)
    args = [
        "--env-file",
        str(tmp_path / "missing.env"),
        "run",
        "start",
        "--golden-set",
        "CLI set",
        "--model",
        "CLI model",
        "--output",
        "json",
    ]
    assert main(args) == 0
    capsys.readouterr()
    invalid_args = list(args)
    invalid_args[7] = "Missing model"
    assert main(invalid_args) == 2
    assert "run lookup" in capsys.readouterr().err

    environment = dict(os.environ)
    app = create_app(
        env_file=None,
        settings_environment=environment,
        initialize_database=True,
    )
    endpoint = app.extensions["endpoint_service"].create(
        {"name": "Second endpoint", "completions_url": "https://second.test/v1/chat/completions"}
    )
    app.extensions["model_profile_service"].create(
        endpoint.id, {"model_id": "duplicate", "display_name": "CLI model"}
    )
    app.extensions["shutdown_services"]()
    assert main(args) == 2
    error = capsys.readouterr().err
    assert "ambiguous" in error
    assert "CLI model" in error


def test_existing_admin_parser_contract_remains_supported() -> None:
    from evaluator.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["migrate"]).command == "migrate"
    assert parser.parse_args(["backup", "out.db"]).destination == Path("out.db")
    restore = parser.parse_args(["restore", "in.db", "out.db"])
    assert (restore.source, restore.destination) == (Path("in.db"), Path("out.db"))
