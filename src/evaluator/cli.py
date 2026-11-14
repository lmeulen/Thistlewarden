"""Database administration and automation-oriented run CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import UUID

from dotenv import load_dotenv

from evaluator import create_app
from evaluator.api.errors import ApiError, ValidationApiError
from evaluator.config import load_bootstrap_settings
from evaluator.database import SQLiteConnectionFactory
from evaluator.errors import ConfigurationError
from evaluator.logging_config import configure_logging, log_event
from evaluator.migrations import MigrationManager, SQLiteBackupService
from evaluator.run_cli import (
    AggregateProtocol,
    ExportProtocol,
    GateProtocol,
    RunExitCode,
    RunViewProtocol,
    WorkerRuntimeProtocol,
    build_summary,
    exit_code_for_summary,
    lookup_records,
    parse_gate_overrides,
    perform_exports,
    render_human_summary,
    render_json,
    resolve_selector,
    wait_for_terminal,
)
from evaluator.runs import RunQueue
from evaluator.runtime import InstanceLock
from evaluator.settings import ApplicationSettings, InMemorySettingsRepository, SettingsResolver

_LOG = logging.getLogger(__name__)
_GATE_ARGUMENTS = (
    "min_weighted_score",
    "min_pass_rate",
    "max_error_rate",
    "max_mean_latency_ms",
    "max_p95_latency_ms",
    "max_p95_ttft_ms",
    "min_mean_tps",
    "max_total_cost",
)


def _settings(env_file: Path | None) -> ApplicationSettings:
    if env_file is not None:
        load_dotenv(env_file, override=True)
    return SettingsResolver(InMemorySettingsRepository()).resolve(os.environ).values


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=("human", "json"), default="human")


def _add_export_arguments(parser: argparse.ArgumentParser, *, long_names: bool) -> None:
    for format_ in ("json", "csv", "html"):
        name = f"--export-{format_}" if long_names else f"--{format_}"
        parser.add_argument(name, dest=f"export_{format_}", type=Path)
    parser.add_argument("--overwrite", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thistlewarden-admin")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="Initialize or migrate configured database")

    backup = subparsers.add_parser("backup", help="Create consistent SQLite backup")
    backup.add_argument("destination", type=Path)

    restore = subparsers.add_parser("restore", help="Restore backup to new database path")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)

    run = subparsers.add_parser("run", help="Create, wait for, inspect, or export runs")
    run_commands = run.add_subparsers(dest="run_command", required=True)

    lookup = run_commands.add_parser("lookup", help="List active run inputs and stable IDs")
    lookup.add_argument("kind", choices=("golden-sets", "models", "judges"))
    _add_output_argument(lookup)

    start = run_commands.add_parser("start", help="Create an immutable evaluation run")
    start.add_argument("--name", help="Run name; defaults to a UTC CLI timestamp")
    start.add_argument("--golden-set", required=True, help="Golden-set UUID or unique exact name")
    start.add_argument(
        "--model",
        action="append",
        required=True,
        help="Model-profile UUID or unique exact name; repeat for multiple models",
    )
    start.add_argument("--judge", help="Judge-profile UUID or unique exact name")
    start.add_argument("--note")
    start.add_argument("--no-gates", action="store_true")
    for name in _GATE_ARGUMENTS:
        start.add_argument(f"--{name.replace('_', '-')}", dest=name)
    start.add_argument("--wait", action="store_true")
    start.add_argument("--poll-interval", type=float, default=0.5)
    start.add_argument("--timeout", type=float)
    start.add_argument("--indeterminate", choices=("fail", "pass"), default="fail")
    _add_output_argument(start)
    _add_export_arguments(start, long_names=True)

    show = run_commands.add_parser("show", help="Show safe run summary")
    show.add_argument("run_id", type=UUID)
    _add_output_argument(show)

    wait = run_commands.add_parser("wait", help="Process and wait for an existing run")
    wait.add_argument("run_id", type=UUID)
    wait.add_argument("--poll-interval", type=float, default=0.5)
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--indeterminate", choices=("fail", "pass"), default="fail")
    _add_output_argument(wait)
    _add_export_arguments(wait, long_names=True)

    export = run_commands.add_parser("export", help="Write complete run reports")
    export.add_argument("run_id", type=UUID)
    _add_export_arguments(export, long_names=False)
    return parser


def _extension(app: Any, name: str) -> Any:
    try:
        return app.extensions[name]
    except KeyError as exc:
        raise ConfigurationError(f"Required run service is unavailable: {name}.") from exc


def _destinations(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        format_: cast(Path | None, getattr(args, f"export_{format_}", None))
        for format_ in ("json", "csv", "html")
    }


def _print_lookup(records: list[dict[str, Any]], output: str) -> None:
    if output == "json":
        print(render_json({"schema_version": "1.0", "items": records}))
        return
    if not records:
        print("No active records found.")
        return
    for record in records:
        details = " · ".join(
            str(value) for key, value in record.items() if key != "id" and value is not None
        )
        print(f"{record['id']}  {details}")


def _print_summary(summary: dict[str, Any], output: str) -> None:
    print(render_json(summary) if output == "json" else render_human_summary(summary))


def _summary_services(app: Any) -> tuple[RunViewProtocol, AggregateProtocol, GateProtocol]:
    return (
        cast(RunViewProtocol, _extension(app, "run_view_service")),
        cast(AggregateProtocol, _extension(app, "aggregate_service")),
        cast(GateProtocol, _extension(app, "quality_gate_service")),
    )


def _build_summary(app: Any, run_id: UUID, outcome: str, exports: list[str]) -> dict[str, Any]:
    view, aggregates, gates = _summary_services(app)
    return build_summary(run_id, view, aggregates, gates, wait_outcome=outcome, exports=exports)


def _export(app: Any, run_id: UUID, args: argparse.Namespace) -> list[str]:
    return perform_exports(
        cast(ExportProtocol, _extension(app, "run_export_service")),
        run_id,
        _destinations(args),
        overwrite=bool(args.overwrite),
    )


def _wait(app: Any, run_id: UUID, args: argparse.Namespace) -> tuple[dict[str, Any], RunExitCode]:
    runtime = cast(WorkerRuntimeProtocol, _extension(app, "worker_runtime"))
    view = cast(RunViewProtocol, _extension(app, "run_view_service"))
    runtime.start()
    outcome = "terminal"
    try:
        _, outcome = wait_for_terminal(
            run_id,
            view,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
    except KeyboardInterrupt:
        outcome = "interrupted"
    finally:
        runtime.stop(timeout=30.0)
    summary = _build_summary(app, run_id, outcome, [])
    if outcome == "terminal":
        try:
            exports = _export(app, run_id, args)
        except (OSError, ApiError) as exc:
            summary["export_error"] = str(exc)
            _print_summary(summary, args.output)
            return summary, RunExitCode.EXPORT_ERROR
        summary["exports"] = exports
    _print_summary(summary, args.output)
    return summary, exit_code_for_summary(summary, indeterminate_policy=args.indeterminate)


def _run_command(args: argparse.Namespace) -> int:
    bootstrap = load_bootstrap_settings(args.env_file)
    with InstanceLock.for_database(bootstrap.database_path):
        app = create_app(
            settings=bootstrap,
            settings_environment=os.environ,
            initialize_database=True,
        )
        try:
            factory = cast(SQLiteConnectionFactory, _extension(app, "database_factory"))
            if args.run_command == "lookup":
                _print_lookup(lookup_records(factory, args.kind), args.output)
                return RunExitCode.SUCCESS
            if args.run_command == "start":
                payload: dict[str, Any] = {
                    "name": args.name
                    or f"CLI run {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
                    "golden_set_id": resolve_selector(factory, "golden set", args.golden_set),
                    "model_profile_ids": [
                        resolve_selector(factory, "model profile", item) for item in args.model
                    ],
                    "note": args.note,
                }
                if args.judge:
                    payload["judge_profile_id"] = resolve_selector(
                        factory, "judge profile", args.judge
                    )
                gates = parse_gate_overrides(vars(args), no_gates=args.no_gates)
                if gates is not None:
                    payload["quality_gates"] = gates
                run_id = cast(RunQueue, _extension(app, "run_queue")).create_run(payload)
                if args.wait:
                    return int(_wait(app, run_id, args)[1])
                summary = _build_summary(app, run_id, "not_requested", [])
                _print_summary(summary, args.output)
                return RunExitCode.SUCCESS
            if args.run_command == "show":
                summary = _build_summary(app, args.run_id, "not_requested", [])
                _print_summary(summary, args.output)
                return RunExitCode.SUCCESS
            if args.run_command == "wait":
                return int(_wait(app, args.run_id, args)[1])
            if args.run_command == "export":
                paths = _export(app, args.run_id, args)
                if not paths:
                    raise ValidationApiError("Select at least one export format.")
                print(render_json({"run_id": str(args.run_id), "exports": paths}))
                return RunExitCode.SUCCESS
            return _unknown_command(args.run_command)
        finally:
            app.extensions["shutdown_services"]()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _run_command(args)
        settings = _settings(args.env_file)
        configure_logging(settings.log_level)
        factory = SQLiteConnectionFactory(settings.database_path)
        if args.command == "migrate":
            log_event(_LOG, logging.INFO, "migration_started", database=settings.database_path)
            MigrationManager(factory).migrate()
            log_event(_LOG, logging.INFO, "migration_completed", database=settings.database_path)
            return 0
        if args.command == "backup":
            SQLiteBackupService(factory).backup(args.destination)
            log_event(_LOG, logging.INFO, "backup_completed", path=args.destination)
            return 0
        if args.command == "restore":
            SQLiteBackupService.restore(args.source, args.destination)
            log_event(_LOG, logging.INFO, "restore_completed", path=args.destination)
            return 0
        return _unknown_command(args.command)
    except (ApiError, ConfigurationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.command == "run" and isinstance(exc, OSError):
            return RunExitCode.EXPORT_ERROR
        if isinstance(exc, ConfigurationError):
            return RunExitCode.RUNTIME_ERROR
        return RunExitCode.USAGE


def _unknown_command(command: str) -> NoReturn:
    raise RuntimeError(f"Unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
