"""Database administration command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv

from evaluator.database import SQLiteConnectionFactory
from evaluator.logging_config import configure_logging, log_event
from evaluator.migrations import MigrationManager, SQLiteBackupService
from evaluator.settings import ApplicationSettings, InMemorySettingsRepository, SettingsResolver

_LOG = logging.getLogger(__name__)


def _settings(env_file: Path | None) -> ApplicationSettings:
    if env_file is not None:
        load_dotenv(env_file, override=True)
    return SettingsResolver(InMemorySettingsRepository()).resolve(os.environ).values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluator.cli")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="Initialize or migrate the configured database")

    backup = subparsers.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("destination", type=Path)

    restore = subparsers.add_parser("restore", help="Restore a backup to a new database path")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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


def _unknown_command(command: str) -> NoReturn:
    raise RuntimeError(f"Unknown command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
