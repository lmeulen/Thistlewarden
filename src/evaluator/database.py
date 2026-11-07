"""SQLite connection and transaction infrastructure."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SQLiteConnectionFactory:
    """Create consistently configured SQLite connections."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms

    def connect(self, *, create_parent: bool = True) -> sqlite3.Connection:
        if create_parent:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        deadline = time.monotonic() + (self.busy_timeout_ms / 1_000)
        while True:
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    connection.close()
                    raise
                time.sleep(0.01)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, mode: str = "IMMEDIATE"
) -> Iterator[sqlite3.Connection]:
    """Commit on success and roll back on every exception."""

    normalized_mode = mode.upper()
    if normalized_mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
        raise ValueError("unsupported transaction mode")
    if connection.in_transaction:
        raise RuntimeError("nested transactions are not supported")

    connection.execute(f"BEGIN {normalized_mode}")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
