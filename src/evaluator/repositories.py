"""SQLite repository adapters."""

from __future__ import annotations

from collections.abc import Mapping

from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.time import to_utc_iso, utc_now


class SQLiteSettingsRepository:
    """Persistent operational settings adapter."""

    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def get(self, name: str) -> str | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT value FROM settings WHERE name = ?", (name,)
            ).fetchone()
            return None if row is None else str(row["value"])
        finally:
            connection.close()

    def set(self, name: str, value: str) -> None:
        now = to_utc_iso(utc_now())
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO settings(name, value, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (name, value, now, now),
                )
        finally:
            connection.close()

    def delete(self, name: str) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute("DELETE FROM settings WHERE name = ?", (name,))
        finally:
            connection.close()

    def all(self) -> Mapping[str, str]:
        connection = self._factory.connect()
        try:
            rows = connection.execute("SELECT name, value FROM settings ORDER BY name").fetchall()
            return {str(row["name"]): str(row["value"]) for row in rows}
        finally:
            connection.close()
