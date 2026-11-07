from __future__ import annotations

import sqlite3
from datetime import UTC
from pathlib import Path

import pytest

from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.time import to_utc_iso, utc_now

pytestmark = pytest.mark.unit


def test_transaction_commits_on_success(tmp_path: Path) -> None:
    connection = SQLiteConnectionFactory(tmp_path / "test.db").connect()
    connection.execute("CREATE TABLE values_table(value TEXT NOT NULL)")

    with transaction(connection):
        connection.execute("INSERT INTO values_table(value) VALUES ('committed')")

    assert connection.execute("SELECT value FROM values_table").fetchone()[0] == "committed"
    connection.close()


def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    connection = SQLiteConnectionFactory(tmp_path / "test.db").connect()
    connection.execute("CREATE TABLE values_table(value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="stop"):
        with transaction(connection):
            connection.execute("INSERT INTO values_table(value) VALUES ('rolled-back')")
            raise RuntimeError("stop")

    assert connection.execute("SELECT COUNT(*) FROM values_table").fetchone()[0] == 0
    connection.close()


def test_connection_enables_required_pragmas(tmp_path: Path) -> None:
    connection = SQLiteConnectionFactory(tmp_path / "test.db", busy_timeout_ms=1234).connect()

    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    connection.close()


def test_foreign_key_violation_fails(tmp_path: Path) -> None:
    connection = SQLiteConnectionFactory(tmp_path / "test.db").connect()
    connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE child(parent_id INTEGER NOT NULL REFERENCES parent(id))")

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")
    connection.close()


def test_utc_helpers_are_timezone_aware_and_normalized() -> None:
    value = utc_now()

    assert value.tzinfo is UTC
    assert value.utcoffset() is not None
    assert to_utc_iso(value).endswith("Z")


def test_naive_datetime_is_rejected() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        to_utc_iso(datetime(2026, 1, 1))
