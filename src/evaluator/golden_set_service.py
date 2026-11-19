"""Golden-set application service."""

# ruff: noqa: S101

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from evaluator.api.errors import ConflictError, ErrorDetail, NotFoundError, ValidationApiError
from evaluator.golden_set_contracts import (
    GoldenItemBulkPreview,
    GoldenItemBulkRequest,
    GoldenSetInput,
    GoldenSetItemInput,
    GoldenSetItemPatch,
    GoldenSetItemRecord,
    GoldenSetLimits,
    GoldenSetPatch,
    GoldenSetRecord,
)
from evaluator.golden_set_io import export_csv, export_json, parse_import
from evaluator.golden_set_repository import SQLiteGoldenSetRepository
from evaluator.logging_config import log_semantic_event
from evaluator.time import to_utc_iso, utc_now

# Preserve stable structured-log component for existing operators and tests.
_LOG = logging.getLogger("evaluator.golden_sets")


class GoldenSetService:
    def __init__(self, repository: SQLiteGoldenSetRepository, limits: GoldenSetLimits) -> None:
        self._repository = repository
        self._limits = limits

    @staticmethod
    def _pagination(page: int, per_page: int) -> None:
        if page < 1 or per_page < 1 or per_page > 100:
            raise ValidationApiError("Invalid pagination; per_page is at most 100.")

    def list_sets(self, page: int, per_page: int) -> tuple[list[GoldenSetRecord], int]:
        self._pagination(page, per_page)
        return self._repository.list_sets(page=page, per_page=per_page)

    def search_sets(
        self,
        page: int,
        per_page: int,
        *,
        query: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[GoldenSetRecord], int]:
        self._pagination(page, per_page)
        normalized_query = query.strip()[:200] if query else None
        return self._repository.search_sets(
            page=page,
            per_page=per_page,
            query=normalized_query or None,
            active_only=active_only,
        )

    def get_set(self, set_id: UUID) -> GoldenSetRecord:
        result = self._repository.get_set(set_id)
        if result is None:
            raise NotFoundError("Golden set not found.")
        return result

    def create_set(self, payload: Any) -> GoldenSetRecord:
        data = self._validate(GoldenSetInput, payload)
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=data, created_at=now, updated_at=now)
        self._save_set(record)
        result = self.get_set(record.id)
        log_semantic_event(_LOG, "golden_set_created", object_id=record.id)
        return result

    def duplicate_set(self, set_id: UUID, name: str) -> GoldenSetRecord:
        source = self.get_set(set_id)
        data = self._validate(
            GoldenSetInput,
            {**source.data.model_dump(), "name": name},
        )
        source_items = self._repository.list_all_items(set_id)
        if len(source_items) > self._limits.max_items:
            raise ConflictError("Golden set contains too many test cases to duplicate.")
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=data, created_at=now, updated_at=now)
        items = [
            GoldenSetItemRecord(
                id=uuid4(),
                golden_set_id=record.id,
                data=item.data,
                created_at=now,
                updated_at=now,
            )
            for item in source_items
        ]
        try:
            self._repository.duplicate_set(set_id, record, items)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-set name already exists.") from exc
        result = self.get_set(record.id)
        log_semantic_event(
            _LOG,
            "golden_set_duplicated",
            object_id=record.id,
            source=set_id,
            case_count=len(items),
        )
        return result

    def update_set(self, set_id: UUID, payload: Any) -> GoldenSetRecord:
        existing = self.get_set(set_id)
        patch = self._validate(GoldenSetPatch, payload)
        merged = existing.data.model_dump()
        merged.update(patch.model_dump(exclude_unset=True))
        data = self._validate(GoldenSetInput, merged)
        record = GoldenSetRecord(
            id=set_id, data=data, created_at=existing.created_at, updated_at=utc_now()
        )
        self._save_set(record)
        result = self.get_set(set_id)
        log_semantic_event(_LOG, "golden_set_updated", object_id=set_id)
        return result

    def _save_set(self, record: GoldenSetRecord) -> None:
        try:
            self._repository.save_set(record)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-set name already exists.") from exc

    def delete_set(self, set_id: UUID) -> bool:
        existing = self.get_set(set_id)
        try:
            if not self._repository.delete_set(set_id):
                raise NotFoundError("Golden set not found.")
        except sqlite3.IntegrityError:
            archived = existing.model_copy(
                update={
                    "data": existing.data.model_copy(update={"active": False}),
                    "updated_at": utc_now(),
                }
            )
            self._repository.save_set(archived)
            log_semantic_event(_LOG, "golden_set_archived", object_id=set_id)
            return False
        log_semantic_event(_LOG, "golden_set_deleted", object_id=set_id)
        return True

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
        self.get_set(set_id)
        self._pagination(page, per_page)
        return self._repository.list_items(
            set_id,
            page=page,
            per_page=per_page,
            category=category,
            tag=tag,
            enabled=enabled,
            evaluation_method=evaluation_method,
            query=query.strip()[:200] if query else None,
        )

    def get_item(self, item_id: UUID) -> GoldenSetItemRecord:
        result = self._repository.get_item(item_id)
        if result is None:
            raise NotFoundError("Test case not found.")
        return result

    def export_set(self, set_id: UUID, format_: Literal["json", "csv"]) -> bytes:
        record = self.get_set(set_id)
        items = [item.data for item in self._repository.list_all_items(set_id)]
        result = export_json(record.data, items) if format_ == "json" else export_csv(items)
        log_semantic_event(
            _LOG,
            "golden_set_exported",
            object_id=set_id,
            format=format_,
            case_count=len(items),
        )
        return result

    def import_set(
        self,
        content: bytes,
        format_: Literal["json", "csv"],
        *,
        csv_set_name: str | None,
        preview: bool,
    ) -> tuple[GoldenSetInput, list[GoldenSetItemInput], GoldenSetRecord | None]:
        if len(content) > self._limits.max_import_file_bytes:
            raise ValidationApiError("Import file exceeds maximum size.")
        try:
            parsed = parse_import(content, format_, csv_set_name)
        except ValueError as exc:
            raise ValidationApiError(str(exc)) from exc
        if len(parsed.item_payloads) > self._limits.max_items:
            raise ValidationApiError("Import contains too many test cases.")
        issues: list[ErrorDetail] = []
        try:
            set_data = cast(GoldenSetInput, self._validate(GoldenSetInput, parsed.set_payload))
        except ValidationApiError as exc:
            issues.extend(
                ErrorDetail(f"set.{detail.field}", detail.message) for detail in exc.details
            )
            set_data = None
        item_data: list[GoldenSetItemInput] = []
        seen: dict[str, int] = {}
        for payload, row in zip(parsed.item_payloads, parsed.row_numbers, strict=True):
            case_id = payload.get("case_id") if isinstance(payload, dict) else None
            label = f"row {row}" + (f" ({case_id})" if isinstance(case_id, str) else "")
            try:
                item = self._validate_item(payload)
                folded = item.case_id.casefold()
                if folded in seen:
                    issues.append(ErrorDetail(label, f"case_id duplicates row {seen[folded]}."))
                else:
                    seen[folded] = row
                item_data.append(item.model_copy(update={"sort_order": len(item_data)}))
            except ValidationApiError as exc:
                if exc.details:
                    issues.extend(
                        ErrorDetail(f"{label}.{detail.field}", detail.message)
                        for detail in exc.details
                    )
                else:
                    issues.append(ErrorDetail(label, exc.message))
        if issues:
            raise ValidationApiError("Import contains invalid data.", details=issues)
        assert set_data is not None
        if preview:
            log_semantic_event(
                _LOG,
                "golden_set_import_previewed",
                format=format_,
                case_count=len(item_data),
                preview=True,
            )
            return set_data, item_data, None
        now = utc_now()
        record = GoldenSetRecord(id=uuid4(), data=set_data, created_at=now, updated_at=now)
        records = [
            GoldenSetItemRecord(
                id=uuid4(),
                golden_set_id=record.id,
                data=item,
                created_at=now,
                updated_at=now,
            )
            for item in item_data
        ]
        try:
            self._repository.import_set(record, records)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Golden-set name or case_id already exists.") from exc
        result = self.get_set(record.id)
        log_semantic_event(
            _LOG,
            "golden_set_imported",
            object_id=record.id,
            format=format_,
            case_count=len(item_data),
            preview=False,
        )
        return set_data, item_data, result

    def create_item(self, set_id: UUID, payload: Any) -> GoldenSetItemRecord:
        self.get_set(set_id)
        if self._repository.count_items(set_id) >= self._limits.max_items:
            raise ConflictError("Maximum number of test cases for golden set was reached.")
        data = self._validate_item(payload)
        data = data.model_copy(update={"sort_order": self._repository.next_sort_order(set_id)})
        now = utc_now()
        record = GoldenSetItemRecord(
            id=uuid4(), golden_set_id=set_id, data=data, created_at=now, updated_at=now
        )
        self._save_item(record)
        result = self.get_item(record.id)
        log_semantic_event(_LOG, "golden_item_created", object_id=record.id, source=set_id)
        return result

    def update_item(self, item_id: UUID, payload: Any) -> GoldenSetItemRecord:
        existing = self.get_item(item_id)
        patch = self._validate(GoldenSetItemPatch, payload)
        merged = existing.data.model_dump()
        merged.update(patch.model_dump(exclude_unset=True))
        data = self._validate_item(merged)
        record = GoldenSetItemRecord(
            id=item_id,
            golden_set_id=existing.golden_set_id,
            data=data,
            created_at=existing.created_at,
            updated_at=utc_now(),
        )
        self._save_item(record, existing.data.sort_order)
        result = self.get_item(item_id)
        log_semantic_event(
            _LOG, "golden_item_updated", object_id=item_id, source=existing.golden_set_id
        )
        return result

    def _save_item(self, record: GoldenSetItemRecord, previous_order: int | None = None) -> None:
        try:
            self._repository.save_item(record, previous_order)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("case_id already exists within golden set.") from exc

    def preview_bulk_items(self, set_id: UUID, payload: Any) -> GoldenItemBulkPreview:
        self.get_set(set_id)
        raw = dict(payload)
        raw["item_ids"] = list(dict.fromkeys(raw.get("item_ids", [])))
        raw["expected_updated_at"] = {}
        request = cast(GoldenItemBulkRequest, self._validate(GoldenItemBulkRequest, raw))
        if len(request.item_ids) > self._limits.max_items:
            raise ValidationApiError("Too many test cases selected.")
        records = [self.get_item(item_id) for item_id in request.item_ids]
        if any(record.golden_set_id != set_id for record in records):
            raise NotFoundError("A selected test case was not found in this golden set.")
        expected = {record.id: record.updated_at for record in records}
        request = request.model_copy(update={"expected_updated_at": expected})
        self._validate_bulk_change(set_id, request, records)
        labels = {
            "enable": ("Enable", ("enabled",)),
            "disable": ("Disable", ("enabled",)),
            "set_category": ("Change category", ("category",)),
            "add_tags": ("Add tags", ("tags",)),
            "remove_tags": ("Remove tags", ("tags",)),
            "replace_tags": ("Replace tags", ("tags",)),
            "set_weight": ("Change weight", ("weight",)),
            "move": ("Move", ("golden set", "order")),
            "duplicate": ("Duplicate", ("golden set", "ID", "order")),
            "reorder": ("Change order", ("order",)),
            "delete": ("Delete", ("test cases",)),
        }
        label, fields = labels[request.action]
        destination = (
            self.get_set(request.destination_set_id)
            if request.destination_set_id is not None
            else None
        )
        return GoldenItemBulkPreview(
            request=request,
            affected_count=len(records),
            action_label=label,
            destination_name=destination.data.name if destination else None,
            changed_fields=fields,
        )

    def apply_bulk_items(self, set_id: UUID, payload: Any) -> int:
        self.get_set(set_id)
        request = cast(GoldenItemBulkRequest, self._validate(GoldenItemBulkRequest, payload))
        if len(request.item_ids) != len(set(request.item_ids)):
            raise ValidationApiError("Duplicate test-case selections are not allowed.")
        if len(request.item_ids) > self._limits.max_items:
            raise ValidationApiError("Too many test cases selected.")
        records = [self.get_item(item_id) for item_id in request.item_ids]
        if any(record.golden_set_id != set_id for record in records):
            raise NotFoundError("A selected test case was not found in this golden set.")
        self._validate_bulk_change(set_id, request, records)
        try:
            count = self._repository.bulk_update_items(
                set_id, request, max_items=self._limits.max_items
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "Bulk change conflicts with an existing case ID, order, or historical reference."
            ) from exc
        log_semantic_event(
            _LOG,
            "golden_items_bulk_deleted"
            if request.action == "delete"
            else "golden_items_bulk_updated",
            object_id=set_id,
            action=request.action,
            count=count,
            source=request.destination_set_id,
        )
        return count

    def _validate_bulk_change(
        self,
        set_id: UUID,
        request: GoldenItemBulkRequest,
        records: list[GoldenSetItemRecord],
    ) -> None:
        for record in records:
            updates: dict[str, Any] = {}
            if request.action in {"enable", "disable"}:
                updates["enabled"] = request.action == "enable"
            elif request.action == "set_category":
                updates["category"] = request.category
            elif request.action == "set_weight":
                updates["weight"] = request.weight
            elif request.action in {"add_tags", "remove_tags", "replace_tags"}:
                if request.action == "add_tags":
                    tags = list(dict.fromkeys([*record.data.tags, *request.tags]))
                elif request.action == "remove_tags":
                    tags = [tag for tag in record.data.tags if tag not in request.tags]
                else:
                    tags = request.tags
                updates["tags"] = tags
            if updates:
                self._validate_item(record.data.model_copy(update=updates).model_dump())
        if request.destination_set_id is not None:
            if request.destination_set_id == set_id:
                raise ValidationApiError("Choose a different destination golden set.")
            self.get_set(request.destination_set_id)
            if (
                self._repository.count_items(request.destination_set_id) + len(records)
                > self._limits.max_items
            ):
                raise ConflictError("Destination golden set would exceed its test-case limit.")
            destination_case_ids = {
                item.data.case_id.casefold()
                for item in self._repository.list_all_items(request.destination_set_id)
            }
            conflicts = [
                record.data.case_id
                for record in records
                if record.data.case_id.casefold() in destination_case_ids
            ]
            if conflicts:
                raise ConflictError(
                    f"Destination already contains {len(conflicts)} selected case ID(s)."
                )

    def delete_items(self, set_id: UUID, item_ids: list[UUID]) -> int:
        self.get_set(set_id)
        unique_ids = list(dict.fromkeys(item_ids))
        if not unique_ids:
            raise ValidationApiError("Select at least one test case.")
        if len(unique_ids) > self._limits.max_items:
            raise ValidationApiError("Too many test cases selected.")
        try:
            deleted = self._repository.delete_items(set_id, unique_ids)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("A test case has historical references.") from exc
        log_semantic_event(_LOG, "golden_items_deleted", object_id=set_id, count=deleted)
        return deleted

    def delete_item(self, item_id: UUID) -> None:
        existing = self.get_item(item_id)
        try:
            if not self._repository.delete_item(item_id):
                raise NotFoundError("Test case not found.")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Test case has historical references.") from exc
        log_semantic_event(
            _LOG, "golden_item_deleted", object_id=item_id, source=existing.golden_set_id
        )

    def _validate_item(self, payload: Any) -> GoldenSetItemInput:
        data = cast(GoldenSetItemInput, self._validate(GoldenSetItemInput, payload))
        if len(data.prompt) > self._limits.max_prompt_chars:
            raise ValidationApiError("Prompt exceeds maximum length.")
        if (
            data.system_prompt is not None
            and len(data.system_prompt) > self._limits.max_system_prompt_chars
        ):
            raise ValidationApiError("System prompt exceeds maximum length.")
        expected_value = (
            data.evaluator_config.get("expected_value")
            if data.evaluation_method in {"exact_match", "llm_judge"}
            else data.expected_answer
        )
        if (
            expected_value is not None
            and len(expected_value) > self._limits.max_expected_answer_chars
        ):
            raise ValidationApiError("Expected answer exceeds maximum length.")
        if data.evaluation_method == "llm_judge":
            size = len(json.dumps(data.evaluator_config, ensure_ascii=False))
            if size > self._limits.max_judge_rubric_context_chars:
                raise ValidationApiError("Judge rubric and context exceed maximum length.")
        return data

    @staticmethod
    def _validate(model: type[BaseModel], payload: Any) -> Any:
        try:
            return model.model_validate(payload)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                raise ValidationApiError.from_pydantic(exc) from exc
            raise ValidationApiError(str(exc)) from exc


def golden_set_to_dict(record: GoldenSetRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        **record.data.model_dump(mode="json"),
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }


def golden_item_to_dict(record: GoldenSetItemRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "golden_set_id": str(record.golden_set_id),
        **record.data.model_dump(mode="json"),
        "created_at": to_utc_iso(record.created_at),
        "updated_at": to_utc_iso(record.updated_at),
    }
