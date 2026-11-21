"""Canonical JSON and CSV interchange for golden sets."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from evaluator.golden_set_contracts import GoldenSetInput, GoldenSetItemInput

CSV_COLUMNS = (
    "case_id",
    "user_prompts",
    "system_prompt",
    "expected_answer",
    "evaluation_method",
    "evaluator_config",
    "category",
    "tags",
    "weight",
    "enabled",
)
_LEGACY_CSV_COLUMNS = (*CSV_COLUMNS[:1], "prompt", *CSV_COLUMNS[2:])


@dataclass(frozen=True, slots=True)
class ImportIssue:
    location: str
    case_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ParsedGoldenSet:
    set_payload: dict[str, Any]
    item_payloads: list[dict[str, Any]]
    row_numbers: list[int]


def canonical_document(data: GoldenSetInput, items: list[GoldenSetItemInput]) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "name": data.name,
        "description": data.description,
        "version": data.version,
        "tags": data.tags,
        "items": [_canonical_item(item) for item in items],
    }


def _canonical_item(item: GoldenSetItemInput) -> dict[str, Any]:
    return {
        "case_id": item.case_id,
        "user_prompts": item.user_prompts,
        "system_prompt": item.system_prompt,
        "expected_answer": item.expected_answer,
        "evaluation_method": item.evaluation_method,
        "evaluator_config": item.evaluator_config,
        "category": item.category,
        "tags": item.tags,
        "weight": str(item.weight),
        "enabled": item.enabled,
    }


def export_json(data: GoldenSetInput, items: list[GoldenSetItemInput]) -> bytes:
    return (
        json.dumps(canonical_document(data, items), ensure_ascii=False, indent=2) + "\n"
    ).encode()


def export_csv(items: list[GoldenSetItemInput]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for item in items:
        row = _canonical_item(item)
        row["system_prompt"] = "" if item.system_prompt is None else item.system_prompt
        row["expected_answer"] = "" if item.expected_answer is None else item.expected_answer
        row["category"] = "" if item.category is None else item.category
        row["user_prompts"] = json.dumps(
            item.user_prompts, ensure_ascii=False, separators=(",", ":")
        )
        row["evaluator_config"] = json.dumps(
            item.evaluator_config, ensure_ascii=False, separators=(",", ":")
        )
        row["tags"] = json.dumps(item.tags, ensure_ascii=False, separators=(",", ":"))
        row["enabled"] = "true" if item.enabled else "false"
        writer.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


def parse_import(
    content: bytes, format_: Literal["json", "csv"], csv_set_name: str | None
) -> ParsedGoldenSet:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("File must contain valid UTF-8.") from exc
    return _parse_json(text) if format_ == "json" else _parse_csv(text, csv_set_name)


def _parse_json(text: str) -> ParsedGoldenSet:
    try:
        document = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}.") from exc
    if not isinstance(document, dict):
        raise ValueError("Top-level JSON value must be an object.")
    allowed = {"schema_version", "name", "description", "version", "tags", "items"}
    unknown = set(document) - allowed
    if unknown:
        raise ValueError(f"Unknown JSON fields: {', '.join(sorted(unknown))}.")
    items = document.get("items")
    if not isinstance(items, list):
        raise ValueError("JSON field items must be a list.")
    schema_version = document.get("schema_version")
    if schema_version not in {None, "2.0"}:
        raise ValueError("Unsupported golden-set JSON schema version.")
    set_payload = {
        key: value for key, value in document.items() if key not in {"items", "schema_version"}
    }
    return ParsedGoldenSet(set_payload, items, list(range(1, len(items) + 1)))


def _parse_csv(text: str, set_name: str | None) -> ParsedGoldenSet:
    if not set_name or not set_name.strip():
        raise ValueError("Form field name is required for CSV import.")
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise ValueError("CSV header is missing.")
    columns = tuple(reader.fieldnames)
    if columns not in {CSV_COLUMNS, _LEGACY_CSV_COLUMNS}:
        raise ValueError("CSV header must contain exactly the documented columns.")
    legacy = columns == _LEGACY_CSV_COLUMNS
    items: list[dict[str, Any]] = []
    rows: list[int] = []
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"CSV row {row_number} contains too many columns.")
        case_id = row.get("case_id", "")
        try:
            config = json.loads(row["evaluator_config"])
            tags = json.loads(row["tags"])
            user_prompts = [row["prompt"]] if legacy else json.loads(row["user_prompts"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"CSV row {row_number} ({case_id}): invalid JSON field.") from exc
        enabled_text = row["enabled"].strip().lower()
        if enabled_text not in {"true", "false"}:
            raise ValueError(f"CSV row {row_number} ({case_id}): enabled must be true or false.")
        items.append(
            {
                "case_id": case_id,
                "user_prompts": user_prompts,
                "system_prompt": row["system_prompt"] or None,
                "expected_answer": row["expected_answer"] or None,
                "evaluation_method": row["evaluation_method"],
                "evaluator_config": config,
                "category": row["category"] or None,
                "tags": tags,
                "weight": row["weight"],
                "enabled": enabled_text == "true",
            }
        )
        rows.append(row_number)
    return ParsedGoldenSet({"name": set_name.strip()}, items, rows)
