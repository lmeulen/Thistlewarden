"""SQLite golden-set repository."""

# ruff: noqa: S101, S608

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.golden_set_contracts import (
    GoldenItemBulkRequest,
    GoldenSetInput,
    GoldenSetItemInput,
    GoldenSetItemRecord,
    GoldenSetRecord,
)
from evaluator.time import to_utc_iso, utc_now

_LOG = logging.getLogger(__name__)


class SQLiteGoldenSetRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_sets(self, *, page: int, per_page: int) -> tuple[list[GoldenSetRecord], int]:
        connection = self._factory.connect()
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM golden_sets").fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM golden_sets ORDER BY name LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_set(row) for row in rows], total

    def search_sets(
        self, *, page: int, per_page: int, query: str | None, active_only: bool
    ) -> tuple[list[GoldenSetRecord], int]:
        clauses: list[str] = []
        values: list[Any] = []
        if query:
            clauses.append("name LIKE ? ESCAPE '\\' COLLATE NOCASE")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"%{escaped}%")
        if active_only:
            clauses.append("active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._factory.connect()
        try:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM golden_sets{where}", values).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM golden_sets{where} ORDER BY name LIMIT ? OFFSET ?",
                (*values, per_page, (page - 1) * per_page),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_set(row) for row in rows], total

    def get_set(self, set_id: UUID) -> GoldenSetRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM golden_sets WHERE id = ?", (str(set_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map_set(row)

    def save_set(self, record: GoldenSetRecord) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    description=excluded.description,version=excluded.version,
                    tags_json=excluded.tags_json,active=excluded.active,
                    updated_at=excluded.updated_at""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
        finally:
            connection.close()

    def import_set(self, record: GoldenSetRecord, items: list[GoldenSetItemRecord]) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
                connection.executemany(
                    "INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            str(item.id),
                            str(record.id),
                            item.data.case_id,
                            item.data.prompt,
                            item.data.system_prompt,
                            item.data.expected_answer,
                            item.data.evaluation_method,
                            json.dumps(
                                item.data.evaluator_config, sort_keys=True, separators=(",", ":")
                            ),
                            item.data.category,
                            json.dumps(item.data.tags),
                            str(item.data.weight),
                            int(item.data.enabled),
                            item.data.sort_order,
                            to_utc_iso(item.created_at),
                            to_utc_iso(item.updated_at),
                        )
                        for item in items
                    ],
                )
        finally:
            connection.close()

    def duplicate_set(
        self,
        source_id: UUID,
        record: GoldenSetRecord,
        items: list[GoldenSetItemRecord],
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                source = connection.execute(
                    "SELECT judge_profile_id,quality_gates_json FROM golden_sets WHERE id=?",
                    (str(source_id),),
                ).fetchone()
                if source is None:
                    raise NotFoundError("Golden set not found.")
                connection.execute(
                    """INSERT INTO golden_sets(
                    id,name,description,version,tags_json,active,created_at,updated_at,
                    judge_profile_id,quality_gates_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(record.id),
                        record.data.name,
                        record.data.description,
                        record.data.version,
                        json.dumps(record.data.tags),
                        int(record.data.active),
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                        source["judge_profile_id"],
                        source["quality_gates_json"],
                    ),
                )
                connection.executemany(
                    "INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            str(item.id),
                            str(record.id),
                            item.data.case_id,
                            item.data.prompt,
                            item.data.system_prompt,
                            item.data.expected_answer,
                            item.data.evaluation_method,
                            json.dumps(
                                item.data.evaluator_config,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            item.data.category,
                            json.dumps(item.data.tags),
                            str(item.data.weight),
                            int(item.data.enabled),
                            item.data.sort_order,
                            to_utc_iso(item.created_at),
                            to_utc_iso(item.updated_at),
                        )
                        for item in items
                    ],
                )
        finally:
            connection.close()

    def delete_set(self, set_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute("DELETE FROM golden_sets WHERE id = ?", (str(set_id),))
            return cursor.rowcount == 1
        finally:
            connection.close()

    def count_items(self, set_id: UUID) -> int:
        connection = self._factory.connect()
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM golden_set_items WHERE golden_set_id = ?", (str(set_id),)
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def list_items(
        self,
        set_id: UUID,
        *,
        page: int,
        per_page: int,
        category: str | None,
        tag: str | None,
        enabled: bool | None,
        evaluation_method: str | None = None,
        query: str | None = None,
    ) -> tuple[list[GoldenSetItemRecord], int]:
        clauses = ["golden_set_id = ?"]
        values: list[Any] = [str(set_id)]
        if category is not None:
            clauses.append("category = ?")
            values.append(category)
        if tag is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(tags_json) WHERE value = ?)")
            values.append(tag)
        if enabled is not None:
            clauses.append("enabled = ?")
            values.append(int(enabled))
        if evaluation_method is not None:
            clauses.append("evaluation_method = ?")
            values.append(evaluation_method)
        if query is not None:
            clauses.append(
                "(case_id LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR prompt LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend((f"%{escaped}%", f"%{escaped}%"))
        where = " AND ".join(clauses)
        connection = self._factory.connect()
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM golden_set_items WHERE {where}", values
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM golden_set_items WHERE {where} "
                "ORDER BY sort_order, case_id LIMIT ? OFFSET ?",
                (*values, per_page, (page - 1) * per_page),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_item(row) for row in rows], total

    def list_all_items(self, set_id: UUID) -> list[GoldenSetItemRecord]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM golden_set_items WHERE golden_set_id = ? "
                "ORDER BY sort_order, case_id",
                (str(set_id),),
            ).fetchall()
        finally:
            connection.close()
        return [self._map_item(row) for row in rows]

    def get_item(self, item_id: UUID) -> GoldenSetItemRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM golden_set_items WHERE id = ?", (str(item_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map_item(row)

    def next_sort_order(self, set_id: UUID) -> int:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM golden_set_items "
                "WHERE golden_set_id = ?",
                (str(set_id),),
            ).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def save_item(self, record: GoldenSetItemRecord, previous_order: int | None = None) -> None:
        data = record.data
        assert data.sort_order is not None
        connection = self._factory.connect()
        try:
            with transaction(connection):
                if previous_order is not None and previous_order != data.sort_order:
                    connection.execute(
                        "UPDATE golden_set_items SET sort_order = 2000000000 WHERE id = ?",
                        (str(record.id),),
                    )
                    low, high = sorted((data.sort_order, previous_order))
                    connection.execute(
                        """UPDATE golden_set_items SET sort_order = sort_order + 1000000
                        WHERE golden_set_id = ? AND id != ?
                        AND sort_order >= ? AND sort_order <= ?""",
                        (str(record.golden_set_id), str(record.id), low, high),
                    )
                    delta = -999999 if data.sort_order < previous_order else -1000001
                    connection.execute(
                        """UPDATE golden_set_items SET sort_order = sort_order + ?
                        WHERE golden_set_id = ? AND id != ?
                        AND sort_order >= ? AND sort_order <= ?""",
                        (
                            delta,
                            str(record.golden_set_id),
                            str(record.id),
                            low + 1000000,
                            high + 1000000,
                        ),
                    )
                connection.execute(
                    """INSERT INTO golden_set_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET case_id=excluded.case_id,prompt=excluded.prompt,
                    system_prompt=excluded.system_prompt,expected_answer=excluded.expected_answer,
                    evaluation_method=excluded.evaluation_method,
                    evaluator_config_json=excluded.evaluator_config_json,
                    category=excluded.category,tags_json=excluded.tags_json,weight=excluded.weight,
                    enabled=excluded.enabled,sort_order=excluded.sort_order,
                    updated_at=excluded.updated_at""",
                    (
                        str(record.id),
                        str(record.golden_set_id),
                        data.case_id,
                        data.prompt,
                        data.system_prompt,
                        data.expected_answer,
                        data.evaluation_method,
                        json.dumps(data.evaluator_config, sort_keys=True, separators=(",", ":")),
                        data.category,
                        json.dumps(data.tags),
                        str(data.weight),
                        int(data.enabled),
                        data.sort_order,
                        to_utc_iso(record.created_at),
                        to_utc_iso(record.updated_at),
                    ),
                )
        finally:
            connection.close()

    def bulk_update_items(
        self,
        set_id: UUID,
        request: GoldenItemBulkRequest,
        *,
        max_items: int,
    ) -> int:
        """Apply one bulk action atomically after rechecking preview versions."""
        connection = self._factory.connect()
        try:
            with transaction(connection):
                ids = [str(item_id) for item_id in request.item_ids]
                placeholders = ",".join("?" for _ in ids)
                rows = connection.execute(
                    f"""SELECT * FROM golden_set_items
                    WHERE golden_set_id=? AND id IN ({placeholders})
                    ORDER BY sort_order,case_id""",
                    (str(set_id), *ids),
                ).fetchall()
                if len(rows) != len(ids):
                    raise NotFoundError("A selected test case was not found in this golden set.")
                for row in rows:
                    expected = request.expected_updated_at.get(UUID(row["id"]))
                    if expected is None or _parse_time(row["updated_at"]) != expected:
                        raise ConflictError(
                            "A selected test case changed after preview. Review again."
                        )

                now = to_utc_iso(utc_now())
                if request.action == "delete":
                    connection.execute(
                        f"DELETE FROM golden_set_items WHERE id IN ({placeholders})",
                        ids,
                    )
                    remaining = connection.execute(
                        "SELECT id FROM golden_set_items WHERE golden_set_id=? "
                        "ORDER BY sort_order,case_id",
                        (str(set_id),),
                    ).fetchall()
                    self._rewrite_orders(
                        connection, [row["id"] for row in remaining], now=now, touched=set()
                    )
                elif request.action in {"enable", "disable"}:
                    connection.execute(
                        f"UPDATE golden_set_items SET enabled=?,updated_at=? "
                        f"WHERE id IN ({placeholders})",
                        (int(request.action == "enable"), now, *ids),
                    )
                elif request.action == "set_category":
                    connection.execute(
                        f"UPDATE golden_set_items SET category=?,updated_at=? "
                        f"WHERE id IN ({placeholders})",
                        (request.category, now, *ids),
                    )
                elif request.action == "set_weight":
                    assert request.weight is not None
                    connection.execute(
                        f"UPDATE golden_set_items SET weight=?,updated_at=? "
                        f"WHERE id IN ({placeholders})",
                        (str(request.weight), now, *ids),
                    )
                elif request.action in {"add_tags", "remove_tags", "replace_tags"}:
                    for row in rows:
                        current = json.loads(row["tags_json"])
                        if request.action == "add_tags":
                            tags = list(dict.fromkeys([*current, *request.tags]))
                        elif request.action == "remove_tags":
                            tags = [tag for tag in current if tag not in request.tags]
                        else:
                            tags = request.tags
                        connection.execute(
                            "UPDATE golden_set_items SET tags_json=?,updated_at=? WHERE id=?",
                            (json.dumps(tags), now, row["id"]),
                        )
                elif request.action == "reorder":
                    assert request.start_order is not None
                    all_rows = connection.execute(
                        "SELECT id FROM golden_set_items WHERE golden_set_id=? "
                        "ORDER BY sort_order,case_id",
                        (str(set_id),),
                    ).fetchall()
                    selected = {row["id"] for row in rows}
                    moving = [row["id"] for row in all_rows if row["id"] in selected]
                    remaining = [row["id"] for row in all_rows if row["id"] not in selected]
                    position = min(request.start_order, len(remaining))
                    ordered = [*remaining[:position], *moving, *remaining[position:]]
                    self._rewrite_orders(connection, ordered, now=now, touched=selected)
                else:
                    assert request.destination_set_id is not None
                    destination = connection.execute(
                        "SELECT id FROM golden_sets WHERE id=?", (str(request.destination_set_id),)
                    ).fetchone()
                    if destination is None:
                        raise NotFoundError("Destination golden set not found.")
                    if request.destination_set_id == set_id:
                        raise ValidationApiError("Choose a different destination golden set.")
                    destination_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM golden_set_items WHERE golden_set_id=?",
                            (str(request.destination_set_id),),
                        ).fetchone()[0]
                    )
                    if destination_count + len(rows) > max_items:
                        raise ConflictError(
                            "Destination golden set would exceed its test-case limit."
                        )
                    if request.action == "move":
                        for offset, row in enumerate(rows):
                            connection.execute(
                                """UPDATE golden_set_items SET golden_set_id=?,
                                sort_order=?,updated_at=? WHERE id=?""",
                                (
                                    str(request.destination_set_id),
                                    destination_count + offset,
                                    now,
                                    row["id"],
                                ),
                            )
                        remaining = connection.execute(
                            "SELECT id FROM golden_set_items WHERE golden_set_id=? "
                            "ORDER BY sort_order,case_id",
                            (str(set_id),),
                        ).fetchall()
                        self._rewrite_orders(
                            connection, [row["id"] for row in remaining], now=now, touched=set()
                        )
                    else:
                        for offset, row in enumerate(rows):
                            values = list(row)
                            values[0] = str(uuid4())
                            values[1] = str(request.destination_set_id)
                            values[12] = destination_count + offset
                            values[13] = now
                            values[14] = now
                            connection.execute(
                                "INSERT INTO golden_set_items "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                values,
                            )
            return len(rows)
        finally:
            connection.close()

    @staticmethod
    def _rewrite_orders(
        connection: sqlite3.Connection,
        ordered_ids: list[str],
        *,
        now: str,
        touched: set[str],
    ) -> None:
        if not ordered_ids:
            return
        maximum = int(
            connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM golden_set_items"
            ).fetchone()[0]
        )
        connection.executemany(
            "UPDATE golden_set_items SET sort_order=? WHERE id=?",
            [(maximum + order + 1, item_id) for order, item_id in enumerate(ordered_ids)],
        )
        connection.executemany(
            "UPDATE golden_set_items SET sort_order=?,updated_at="
            "CASE WHEN ? THEN ? ELSE updated_at END WHERE id=?",
            [
                (order, int(item_id in touched), now, item_id)
                for order, item_id in enumerate(ordered_ids)
            ],
        )

    def delete_items(self, set_id: UUID, item_ids: list[UUID]) -> int:
        if not item_ids:
            return 0
        connection = self._factory.connect()
        try:
            with transaction(connection):
                placeholders = ",".join("?" for _ in item_ids)
                values = [str(set_id), *(str(item_id) for item_id in item_ids)]
                rows = connection.execute(
                    f"""SELECT id FROM golden_set_items
                    WHERE golden_set_id=? AND id IN ({placeholders})""",
                    values,
                ).fetchall()
                if len(rows) != len(item_ids):
                    raise NotFoundError("A selected test case was not found.")
                connection.execute(
                    f"""DELETE FROM golden_set_items
                    WHERE golden_set_id=? AND id IN ({placeholders})""",
                    values,
                )
                remaining = connection.execute(
                    """SELECT id FROM golden_set_items WHERE golden_set_id=?
                    ORDER BY sort_order,case_id""",
                    (str(set_id),),
                ).fetchall()
                connection.executemany(
                    "UPDATE golden_set_items SET sort_order=? WHERE id=?",
                    [(index, row["id"]) for index, row in enumerate(remaining)],
                )
            return len(rows)
        finally:
            connection.close()

    def delete_item(self, item_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                row = connection.execute(
                    "SELECT golden_set_id, sort_order FROM golden_set_items WHERE id = ?",
                    (str(item_id),),
                ).fetchone()
                if row is None:
                    return False
                connection.execute("DELETE FROM golden_set_items WHERE id = ?", (str(item_id),))
                connection.execute(
                    """UPDATE golden_set_items SET sort_order = sort_order - 1
                    WHERE golden_set_id = ? AND sort_order > ?""",
                    (row["golden_set_id"], row["sort_order"]),
                )
            return True
        finally:
            connection.close()

    @staticmethod
    def _map_set(row: sqlite3.Row) -> GoldenSetRecord:
        return GoldenSetRecord(
            id=UUID(row["id"]),
            data=GoldenSetInput(
                name=row["name"],
                description=row["description"],
                version=row["version"],
                tags=json.loads(row["tags_json"]),
                active=bool(row["active"]),
            ),
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
        )

    @staticmethod
    def _map_item(row: sqlite3.Row) -> GoldenSetItemRecord:
        data = GoldenSetItemInput(
            case_id=row["case_id"],
            prompt=row["prompt"],
            system_prompt=row["system_prompt"],
            expected_answer=row["expected_answer"],
            evaluation_method=row["evaluation_method"],
            evaluator_config=json.loads(row["evaluator_config_json"]),
            category=row["category"],
            tags=json.loads(row["tags_json"]),
            weight=Decimal(row["weight"]),
            enabled=bool(row["enabled"]),
            sort_order=row["sort_order"],
        )
        return GoldenSetItemRecord(
            id=UUID(row["id"]),
            golden_set_id=UUID(row["golden_set_id"]),
            data=data,
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
        )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
