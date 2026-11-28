"""Versioned, secret-free functional configuration exchange."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evaluator.api.errors import ConflictError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.endpoints import EndpointInput
from evaluator.gates import QualityGates
from evaluator.golden_set_contracts import GoldenSetInput, GoldenSetItemInput
from evaluator.judging.definitions import (
    JudgeDefinitionInput,
    JudgeDefinitionVersion,
    semantic_hash,
)
from evaluator.judging.extensions import JudgeImplementationRegistry
from evaluator.judging.profiles import JudgeProfileInput
from evaluator.logging_config import log_semantic_event
from evaluator.models import ModelProfileInput
from evaluator.time import to_utc_iso, utc_now

RestoreMode = Literal["merge", "replace", "archive_all"]

_LOG = logging.getLogger(__name__)


class BundleObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    data: dict[str, Any]


class RunBundle(BaseModel):
    """One immutable run and all durable evidence needed to read it."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    data: dict[str, Any]
    case_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    model_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    quality_gates: list[dict[str, Any]] = Field(default_factory=list)


class JudgeDefinitionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=100)
    draft: JudgeDefinitionInput | None = None
    versions: list[JudgeDefinitionVersion] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_identity(self) -> JudgeDefinitionBundle:
        if any(version.definition_id != self.id for version in self.versions):
            raise ValueError("judge version definition reference does not match")
        if self.draft is None and not self.versions:
            raise ValueError("judge definition must contain a draft or published version")
        return self


_RUN_TABLE_COLUMNS = {
    "runs": (
        "id",
        "name",
        "golden_set_id",
        "status",
        "judge_profile_id",
        "note",
        "cancelled_at",
        "created_at",
        "started_at",
        "completed_at",
        "recovery_warning",
        "judge_snapshot_json",
        "quality_gates_snapshot_json",
        "gate_status",
    ),
    "run_case_snapshots": (
        "id",
        "run_id",
        "source_item_id",
        "case_id",
        "weight",
        "snapshot_json",
    ),
    "run_model_snapshots": (
        "id",
        "run_id",
        "source_profile_id",
        "endpoint_id",
        "snapshot_json",
    ),
    "run_jobs": (
        "id",
        "run_id",
        "case_snapshot_id",
        "model_snapshot_id",
        "status",
        "attempt_count",
        "lease_owner",
        "lease_generation",
        "lease_expires_at",
        "recovered_after_interruption",
        "error_code",
        "created_at",
        "updated_at",
    ),
    "run_attempts": (
        "id",
        "job_id",
        "attempt_number",
        "category",
        "metadata_json",
        "created_at",
    ),
    "run_job_turns": (
        "id",
        "job_id",
        "turn_index",
        "lease_generation",
        "user_prompt",
        "status",
        "assistant_response",
        "metrics_json",
        "attempts_json",
        "provider_evidence_json",
        "started_at",
        "completed_at",
    ),
    "run_results": (
        "id",
        "job_id",
        "run_id",
        "model_snapshot_id",
        "case_snapshot_id",
        "status",
        "score",
        "weight",
        "result_json",
        "target_cost",
        "judge_cost",
        "target_latency_ms",
        "ttft_ms",
        "tps",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "created_at",
    ),
    "quality_gate_results": (
        "id",
        "run_id",
        "model_snapshot_id",
        "gate",
        "measured_value",
        "threshold_value",
        "outcome",
        "created_at",
    ),
}


BACKUP_CATEGORIES = (
    "endpoints",
    "model_profiles",
    "judge_profiles",
    "judge_definitions",
    "golden_sets",
    "golden_set_items",
    "runs",
)


class BackupCategoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: bool = False
    included: list[str] = Field(default_factory=list)
    omitted_count: int = Field(default=0, ge=0)
    automatic_dependencies: list[str] = Field(default_factory=list)


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal["1.1"] = "1.1"
    created_at: datetime
    integrity_algorithm: Literal["sha256"] = "sha256"
    categories: dict[str, BackupCategoryManifest]
    integrity: dict[str, str]

    @model_validator(mode="after")
    def complete_categories(self) -> BackupManifest:
        if set(self.categories) != set(BACKUP_CATEGORIES):
            raise ValueError("backup manifest categories are incomplete")
        if set(self.integrity) != set(BACKUP_CATEGORIES):
            raise ValueError("backup manifest integrity metadata is incomplete")
        return self


class BackupSelection(BaseModel):
    """Explicit categories and records requested for a selective backup."""

    model_config = ConfigDict(extra="forbid")
    categories: set[str] = Field(default_factory=set)
    item_ids: dict[str, set[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_categories(self) -> BackupSelection:
        if not self.categories <= set(BACKUP_CATEGORIES):
            raise ValueError("unknown backup category")
        if not set(self.item_ids) <= set(BACKUP_CATEGORIES):
            raise ValueError("unknown backup item category")
        return self


class ConfigurationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0", "1.1"] = "1.0"
    manifest: BackupManifest | None = None
    endpoints: list[BundleObject] = Field(default_factory=list)
    model_profiles: list[BundleObject] = Field(default_factory=list)
    judge_profiles: list[BundleObject] = Field(default_factory=list)
    judge_definitions: list[JudgeDefinitionBundle] = Field(default_factory=list)
    golden_sets: list[BundleObject] = Field(default_factory=list)
    golden_set_items: list[BundleObject] = Field(default_factory=list)
    runs: list[RunBundle] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids_and_references(self) -> ConfigurationBundle:
        if (self.schema_version == "1.1") != (self.manifest is not None):
            raise ValueError("selective backup manifest does not match schema version")
        groups = [
            self.endpoints,
            self.model_profiles,
            self.judge_profiles,
            self.golden_sets,
            self.golden_set_items,
        ]
        all_ids = [item.id for group in groups for item in group]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("object UUIDs must be globally unique")
        run_ids = [item.id for item in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run IDs must be unique")
        definition_ids = [item.id for item in self.judge_definitions]
        if len(definition_ids) != len(set(definition_ids)):
            raise ValueError("judge definition IDs must be unique")
        endpoint_ids = {str(x.id) for x in self.endpoints}
        judge_ids = {str(x.id) for x in self.judge_profiles}
        set_ids = {str(x.id) for x in self.golden_sets}
        for item in self.model_profiles:
            if str(item.data.get("endpoint_id")) not in endpoint_ids:
                raise ValueError("model endpoint reference is missing")
        for item in self.golden_sets:
            ref = item.data.get("judge_profile_id")
            if ref is not None and str(ref) not in judge_ids:
                raise ValueError("golden-set judge reference is missing")
        for item in self.golden_set_items:
            if str(item.data.get("golden_set_id")) not in set_ids:
                raise ValueError("item set reference is missing")
        if self.manifest is not None:
            data = self.model_dump(mode="json", exclude={"manifest"})
            for category in BACKUP_CATEGORIES:
                entry = self.manifest.categories[category]
                actual_ids = {str(item["id"]) for item in data[category]}
                if actual_ids != set(entry.included):
                    raise ValueError("backup manifest contents do not match")
                if not set(entry.automatic_dependencies) <= actual_ids:
                    raise ValueError("backup manifest dependencies do not match")
                if self.manifest.integrity[category] != _category_hash(data[category]):
                    raise ValueError("backup manifest integrity check failed")
        return self


class ImportPlan(BaseModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    mode: RestoreMode
    creates: int
    updates: int
    archives: int
    uuid_map: dict[str, str]
    conflicts: list[dict[str, str]]
    missing_secrets: list[str]


def _category_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _plain(row: sqlite3.Row, excluded: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in row.keys()
        if key not in excluded | {"id", "created_at", "updated_at"}
    }
    for key in tuple(result):
        if key.endswith("_json"):
            encoded = result.pop(key)
            result[key.removesuffix("_json")] = None if encoded is None else json.loads(encoded)
        elif key in {
            "active",
            "trusted_internal_http",
            "security_warning_confirmed",
            "send_reasoning_off",
            "is_global_default",
            "enabled",
        }:
            result[key] = bool(result[key])
    return result


class ConfigurationExchangeService:
    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        judge_registry: JudgeImplementationRegistry | None = None,
    ) -> None:
        self._factory = factory
        self._judge_registry = judge_registry

    def export(self, selection: BackupSelection | None = None) -> ConfigurationBundle:
        c = self._factory.connect()
        try:
            endpoints = []
            for row in c.execute("SELECT * FROM endpoints ORDER BY id"):
                data = _plain(row, {"api_key_secret_id", "oauth_client_secret_id"})
                data["api_key"] = None
                data["oauth_client_secret"] = None
                headers = []
                for h in c.execute(
                    "SELECT name,value,secret_id FROM endpoint_headers "
                    "WHERE endpoint_id=? ORDER BY name",
                    (row["id"],),
                ):
                    headers.append(
                        {
                            "name": h["name"],
                            "value": None if h["secret_id"] else h["value"],
                            "secret": h["secret_id"] is not None,
                        }
                    )
                data["extra_headers"] = headers
                data["secret_missing"] = (
                    row["api_key_secret_id"] is not None
                    or row["oauth_client_secret_id"] is not None
                )
                endpoints.append(BundleObject(id=row["id"], data=data))
            models = [
                BundleObject(id=r["id"], data={**_plain(r), "endpoint_id": r["endpoint_id"]})
                for r in c.execute("SELECT * FROM model_profiles ORDER BY id")
            ]
            judges = []
            for row in c.execute("SELECT * FROM judge_profiles ORDER BY id"):
                data = _plain(
                    row,
                    {
                        "prompt_template",
                        "template_version",
                        "threshold",
                        "rubric_json",
                        "temperature_enabled",
                        "implementation_config_json",
                    },
                )
                if not row["temperature_enabled"]:
                    data["temperature"] = None
                data["implementation_config"] = json.loads(row["implementation_config_json"])
                judges.append(BundleObject(id=row["id"], data=data))
            definitions = []
            for row in c.execute("SELECT * FROM judge_definitions ORDER BY id"):
                versions = [
                    JudgeDefinitionVersion(
                        definition_id=version["definition_id"],
                        version=version["version"],
                        origin="local",
                        lifecycle="published",
                        data=JudgeDefinitionInput.model_validate_json(version["data_json"]),
                        semantic_hash=version["semantic_hash"],
                    )
                    for version in c.execute(
                        "SELECT * FROM judge_definition_versions WHERE definition_id=? "
                        "ORDER BY version",
                        (row["id"],),
                    )
                ]
                definitions.append(
                    JudgeDefinitionBundle(
                        id=row["id"],
                        draft=(
                            JudgeDefinitionInput.model_validate_json(row["draft_json"])
                            if row["draft_json"]
                            else None
                        ),
                        versions=versions,
                    )
                )
            sets = [
                BundleObject(id=r["id"], data=_plain(r))
                for r in c.execute("SELECT * FROM golden_sets ORDER BY id")
            ]
            items = [
                BundleObject(id=r["id"], data=_plain(r))
                for r in c.execute(
                    "SELECT * FROM golden_set_items ORDER BY golden_set_id,sort_order"
                )
            ]
            runs = (
                [
                    self._export_run(c, row)
                    for row in c.execute(
                        "SELECT * FROM runs WHERE status IN "
                        "('completed','completed_with_errors','failed','cancelled') ORDER BY id"
                    )
                ]
                if selection is not None
                else []
            )
            bundle = ConfigurationBundle(
                endpoints=endpoints,
                model_profiles=models,
                judge_profiles=judges,
                judge_definitions=definitions,
                golden_sets=sets,
                golden_set_items=items,
                runs=runs,
            )
            if selection is not None:
                bundle = self._select(bundle, selection)
        finally:
            c.close()
        log_semantic_event(
            _LOG,
            "configuration_exported",
            object_count=sum(
                len(group)
                for group in (
                    bundle.endpoints,
                    bundle.model_profiles,
                    bundle.judge_profiles,
                    bundle.judge_definitions,
                    bundle.golden_sets,
                    bundle.golden_set_items,
                    bundle.runs,
                )
            ),
        )
        return bundle

    @staticmethod
    def _export_run(c: sqlite3.Connection, row: sqlite3.Row) -> RunBundle:
        run_id = row["id"]

        def rows(table: str, order: str = "id") -> list[dict[str, Any]]:
            return [
                dict(item)
                for item in c.execute(
                    f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order}", (run_id,)
                )
            ]

        attempts = [
            dict(item)
            for item in c.execute(
                "SELECT a.* FROM run_attempts a JOIN run_jobs j ON j.id=a.job_id "
                "WHERE j.run_id=? ORDER BY a.job_id,a.attempt_number",
                (run_id,),
            )
        ]
        turns = [
            dict(item)
            for item in c.execute(
                "SELECT t.* FROM run_job_turns t JOIN run_jobs j ON j.id=t.job_id "
                "WHERE j.run_id=? ORDER BY t.job_id,t.turn_index",
                (run_id,),
            )
        ]
        return RunBundle(
            id=run_id,
            data=dict(row),
            case_snapshots=rows("run_case_snapshots"),
            model_snapshots=rows("run_model_snapshots"),
            jobs=rows("run_jobs"),
            attempts=attempts,
            turns=turns,
            results=rows("run_results", "job_id"),
            quality_gates=rows("quality_gate_results", "model_snapshot_id,gate"),
        )

    def selection_options(self) -> dict[str, list[dict[str, str]]]:
        """Return safe labels and stable IDs for every selectable backup record."""
        bundle = self.export()
        connection = self._factory.connect()
        try:
            runs = [
                {"id": str(row["id"]), "label": str(row["name"])}
                for row in connection.execute(
                    "SELECT id,name FROM runs WHERE status IN "
                    "('completed','completed_with_errors','failed','cancelled') "
                    "ORDER BY created_at DESC,id"
                )
            ]
        finally:
            connection.close()
        return {
            "endpoints": [
                {"id": str(item.id), "label": str(item.data["name"])} for item in bundle.endpoints
            ],
            "model_profiles": [
                {"id": str(item.id), "label": str(item.data["display_name"])}
                for item in bundle.model_profiles
            ],
            "judge_profiles": [
                {"id": str(item.id), "label": str(item.data["name"])}
                for item in bundle.judge_profiles
            ],
            "judge_definitions": [
                {
                    "id": item.id,
                    "label": str(
                        item.draft.name if item.draft is not None else item.versions[-1].data.name
                    ),
                }
                for item in bundle.judge_definitions
            ],
            "golden_sets": [
                {"id": str(item.id), "label": str(item.data["name"])} for item in bundle.golden_sets
            ],
            "golden_set_items": [
                {"id": str(item.id), "label": str(item.data["case_id"])}
                for item in bundle.golden_set_items
            ],
            "runs": runs,
        }

    @staticmethod
    def _select(bundle: ConfigurationBundle, selection: BackupSelection) -> ConfigurationBundle:
        groups: dict[str, list[Any]] = {
            category: list(getattr(bundle, category)) for category in BACKUP_CATEGORIES
        }
        all_ids = {category: {str(item.id) for item in items} for category, items in groups.items()}
        explicit = {
            category: (
                set(all_ids[category])
                if category in selection.categories
                else set(selection.item_ids.get(category, set()))
            )
            for category in BACKUP_CATEGORIES
        }
        if any(not explicit[name] <= all_ids[name] for name in BACKUP_CATEGORIES):
            raise ValidationApiError("A selected backup record no longer exists.")
        included = {name: set(values) for name, values in explicit.items()}
        by_id = {
            category: {str(item.id): item for item in items} for category, items in groups.items()
        }

        changed = True
        while changed:
            before = sum(len(values) for values in included.values())
            for item_id in tuple(included["golden_set_items"]):
                parent = str(by_id["golden_set_items"][item_id].data["golden_set_id"])
                included["golden_sets"].add(parent)
            for set_id in tuple(included["golden_sets"]):
                golden_set = by_id["golden_sets"][set_id]
                if set_id in explicit["golden_sets"]:
                    included["golden_set_items"].update(
                        item_id
                        for item_id, item in by_id["golden_set_items"].items()
                        if str(item.data["golden_set_id"]) == set_id
                    )
                judge_id = golden_set.data.get("judge_profile_id")
                if judge_id is not None:
                    included["judge_profiles"].add(str(judge_id))
            for item_id in tuple(included["golden_set_items"]):
                item = by_id["golden_set_items"][item_id]
                config = item.data.get("evaluator_config") or {}
                definition_id = config.get("judge_definition_id")
                if definition_id is not None and str(definition_id) in all_ids["judge_definitions"]:
                    included["judge_definitions"].add(str(definition_id))
            for profile_id in tuple(included["judge_profiles"]):
                model_id = str(by_id["judge_profiles"][profile_id].data["model_profile_id"])
                included["model_profiles"].add(model_id)
            for model_id in tuple(included["model_profiles"]):
                endpoint_id = str(by_id["model_profiles"][model_id].data["endpoint_id"])
                included["endpoints"].add(endpoint_id)
            changed = before != sum(len(values) for values in included.values())

        selected_data = {
            category: [item for item in groups[category] if str(item.id) in included[category]]
            for category in BACKUP_CATEGORIES
        }
        manifest_categories = {
            category: BackupCategoryManifest(
                selected=category in selection.categories,
                included=sorted(included[category]),
                omitted_count=len(all_ids[category] - included[category]),
                automatic_dependencies=sorted(included[category] - explicit[category]),
            )
            for category in BACKUP_CATEGORIES
        }
        wire_data = {
            category: [item.model_dump(mode="json") for item in selected_data[category]]
            for category in BACKUP_CATEGORIES
        }
        manifest = BackupManifest(
            created_at=utc_now(),
            categories=manifest_categories,
            integrity={
                category: _category_hash(wire_data[category]) for category in BACKUP_CATEGORIES
            },
        )
        return ConfigurationBundle(
            schema_version="1.1",
            manifest=manifest,
            **selected_data,
        )

    def parse(self, payload: Any) -> ConfigurationBundle:
        try:
            bundle = ConfigurationBundle.model_validate(payload)
            for item in bundle.endpoints:
                d = dict(item.data)
                secret_missing = bool(d.pop("secret_missing", None))
                d.pop("oauth_client_secret", None)
                if d.get("auth_type") == "oauth_client_credentials" and secret_missing:
                    d["oauth_client_secret"] = "********"  # noqa: S105
                for h in d.get("extra_headers", []):
                    if h.get("secret") and h.get("value") is None:
                        h["value"] = ""
                EndpointInput.model_validate(d)
            for item in bundle.model_profiles:
                d = dict(item.data)
                d.pop("endpoint_id", None)
                ModelProfileInput.model_validate(_model_wire(d))
            bundled_model_ids = {str(item.id) for item in bundle.model_profiles}
            connection = self._factory.connect()
            try:
                existing_model_ids = {
                    str(row[0]) for row in connection.execute("SELECT id FROM model_profiles")
                }
            finally:
                connection.close()
            for item in bundle.judge_profiles:
                judge = JudgeProfileInput.model_validate(item.data)
                if str(judge.model_profile_id) not in bundled_model_ids | existing_model_ids:
                    raise ValueError("judge model reference is missing")
                if self._judge_registry is not None:
                    self._judge_registry.validate_configuration(
                        judge.implementation_id,
                        judge.implementation_version,
                        judge.implementation_config,
                    )
            for definition in bundle.judge_definitions:
                for version in definition.versions:
                    if version.semantic_hash != semantic_hash(version.data):
                        raise ValueError("judge version semantic hash does not match")
            for item in bundle.golden_sets:
                GoldenSetInput.model_validate(
                    {
                        k: v
                        for k, v in item.data.items()
                        if k not in {"judge_profile_id", "quality_gates"}
                    }
                )
                QualityGates.model_validate(item.data.get("quality_gates") or {})
            for item in bundle.golden_set_items:
                item_data = {k: v for k, v in item.data.items() if k != "golden_set_id"}
                if "user_prompts" in item_data:
                    item_data.pop("prompt", None)
                GoldenSetItemInput.model_validate(item_data)
            for run in bundle.runs:
                self._validate_run(run)
            return bundle
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValidationApiError("Configuration file is invalid.") from exc

    @staticmethod
    def _validate_run(run: RunBundle) -> None:
        rows_by_table = {
            "runs": [run.data],
            "run_case_snapshots": run.case_snapshots,
            "run_model_snapshots": run.model_snapshots,
            "run_jobs": run.jobs,
            "run_attempts": run.attempts,
            "run_job_turns": run.turns,
            "run_results": run.results,
            "quality_gate_results": run.quality_gates,
        }
        if any(
            set(row) != set(_RUN_TABLE_COLUMNS[table])
            for table, rows in rows_by_table.items()
            for row in rows
        ):
            raise ValueError("run evidence columns do not match")
        if str(run.data.get("id")) != str(run.id):
            raise ValueError("run identity does not match")
        if run.data.get("status") not in {
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        }:
            raise ValueError("only terminal runs can be restored")
        case_ids = {str(item.get("id")) for item in run.case_snapshots}
        model_ids = {str(item.get("id")) for item in run.model_snapshots}
        job_ids = {str(item.get("id")) for item in run.jobs}
        if any(str(item.get("run_id")) != str(run.id) for item in run.case_snapshots):
            raise ValueError("run case snapshot reference does not match")
        if any(str(item.get("run_id")) != str(run.id) for item in run.model_snapshots):
            raise ValueError("run model snapshot reference does not match")
        if any(
            str(item.get("run_id")) != str(run.id)
            or str(item.get("case_snapshot_id")) not in case_ids
            or str(item.get("model_snapshot_id")) not in model_ids
            for item in run.jobs
        ):
            raise ValueError("run job reference does not match")
        if any(str(item.get("job_id")) not in job_ids for item in (*run.attempts, *run.turns)):
            raise ValueError("run job evidence reference does not match")
        if any(
            str(item.get("run_id")) != str(run.id)
            or str(item.get("job_id")) not in job_ids
            or str(item.get("case_snapshot_id")) not in case_ids
            or str(item.get("model_snapshot_id")) not in model_ids
            for item in run.results
        ):
            raise ValueError("run result reference does not match")
        if any(
            str(item.get("run_id")) != str(run.id)
            or (
                item.get("model_snapshot_id") is not None
                and str(item["model_snapshot_id"]) not in model_ids
            )
            for item in run.quality_gates
        ):
            raise ValueError("run quality-gate reference does not match")

    def plan(self, bundle: ConfigurationBundle, mode: RestoreMode) -> ImportPlan:
        c = self._factory.connect()
        try:
            existing = {
                r[0]
                for table in (
                    "endpoints",
                    "model_profiles",
                    "judge_profiles",
                    "golden_sets",
                    "golden_set_items",
                )
                for r in c.execute(f"SELECT id FROM {table}")
            }
            incoming = [
                x
                for group in (
                    bundle.endpoints,
                    bundle.model_profiles,
                    bundle.judge_profiles,
                    bundle.golden_sets,
                    bundle.golden_set_items,
                )
                for x in group
            ]
            mapping = {str(x.id): str(uuid4()) for x in incoming if str(x.id) in existing}
            conflicts = [{"id": old, "resolution": f"remap:{new}"} for old, new in mapping.items()]
            existing_definitions = {row[0] for row in c.execute("SELECT id FROM judge_definitions")}
            existing_runs = {row[0] for row in c.execute("SELECT id FROM runs")}
            conflicts.extend(
                {"id": item.id, "resolution": "reject:immutable-definition-exists"}
                for item in bundle.judge_definitions
                if item.id in existing_definitions
            )
            conflicts.extend(
                {"id": str(item.id), "resolution": "reject:run-exists"}
                for item in bundle.runs
                if str(item.id) in existing_runs
            )
            missing = [
                str(x.id)
                for x in bundle.endpoints
                if x.data.get("secret_missing")
                or any(h.get("secret") for h in x.data.get("extra_headers", []))
            ]
            archives = 0
            if mode in {"replace", "archive_all"}:
                archive_categories = (
                    self._archivable_categories()
                    if mode == "archive_all"
                    else self._replace_categories(bundle)
                )
                archives = sum(
                    c.execute(f"SELECT COUNT(*) FROM {table} WHERE active=1").fetchone()[0]
                    for table in ("endpoints", "model_profiles", "judge_profiles", "golden_sets")
                    if table in archive_categories
                )
            return ImportPlan(
                schema_version=bundle.schema_version,
                mode=mode,
                creates=len(incoming) + len(bundle.judge_definitions) + len(bundle.runs),
                updates=0,
                archives=archives,
                uuid_map=mapping,
                conflicts=conflicts,
                missing_secrets=missing,
            )
        finally:
            c.close()

    def import_bundle(self, payload: Any, mode: RestoreMode, *, preview: bool) -> ImportPlan:
        bundle = self.parse(payload)
        plan = self.plan(bundle, mode)
        if preview:
            log_semantic_event(
                _LOG,
                "configuration_import_previewed",
                mode=mode,
                object_count=plan.creates,
                conflicts=len(plan.conflicts),
                missing_count=len(plan.missing_secrets),
                preview=True,
            )
            return plan
        if any(
            conflict["resolution"] in {"reject:immutable-definition-exists", "reject:run-exists"}
            for conflict in plan.conflicts
        ):
            raise ConflictError("A Judge definition or run from this backup already exists.")
        c = self._factory.connect()
        now = to_utc_iso(utc_now())
        try:
            with transaction(c):
                if mode in {"replace", "archive_all"}:
                    suffix = f" [archived {now}]"
                    archive_categories = (
                        self._archivable_categories()
                        if mode == "archive_all"
                        else self._replace_categories(bundle)
                    )
                    if "golden_sets" in archive_categories:
                        c.execute(
                            "UPDATE golden_sets SET active=0,name=name||?,updated_at=? "
                            "WHERE active=1",
                            (suffix, now),
                        )
                    if "judge_profiles" in archive_categories:
                        c.execute(
                            "UPDATE judge_profiles SET active=0,is_global_default=0,name=name||?,"
                            "updated_at=? WHERE active=1",
                            (suffix, now),
                        )
                    if "model_profiles" in archive_categories:
                        c.execute(
                            "UPDATE model_profiles SET active=0,display_name=display_name||?,"
                            "updated_at=? WHERE active=1",
                            (suffix, now),
                        )
                    if "endpoints" in archive_categories:
                        c.execute(
                            "UPDATE endpoints SET active=0,name=name||?,updated_at=? "
                            "WHERE active=1",
                            (suffix, now),
                        )
                self._write(c, bundle, plan.uuid_map, mode, now)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Configuration import conflicts with existing names.") from exc
        finally:
            c.close()
        log_semantic_event(
            _LOG,
            "configuration_imported",
            mode=mode,
            object_count=plan.creates,
            conflicts=len(plan.conflicts),
            missing_count=len(plan.missing_secrets),
            preview=False,
        )
        return plan

    @staticmethod
    def _archivable_categories() -> set[str]:
        return {"endpoints", "model_profiles", "judge_profiles", "golden_sets"}

    @classmethod
    def _replace_categories(cls, bundle: ConfigurationBundle) -> set[str]:
        if bundle.manifest is None:
            return cls._archivable_categories()
        return {
            category
            for category, entry in bundle.manifest.categories.items()
            if entry.selected or bool(set(entry.included) - set(entry.automatic_dependencies))
        }

    def _write(
        self, c: sqlite3.Connection, b: ConfigurationBundle, m: dict[str, str], mode: str, now: str
    ) -> None:
        def rid(value: object) -> str:
            return m.get(str(value), str(value))

        verb = "INSERT"
        for x in b.endpoints:
            d = x.data
            c.execute(
                f"{verb} INTO endpoints("
                "id,name,completions_url,models_url,api_key_secret_id,api_key_header,"
                "api_key_prefix,timeout_seconds,max_retries,requests_per_minute,"
                "tokens_per_minute,max_concurrency,active,trusted_internal_http,"
                "security_warning_confirmed,created_at,updated_at,api_format,auth_type,oauth_token_url,"
                "oauth_client_id,oauth_client_secret_id,oauth_scope"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid(x.id),
                    d["name"],
                    d["completions_url"],
                    d.get("models_url"),
                    None,
                    d.get("api_key_header", "Authorization"),
                    d.get("api_key_prefix", ""),
                    d.get("timeout_seconds", 60),
                    d.get("max_retries", 2),
                    d.get("requests_per_minute"),
                    d.get("tokens_per_minute"),
                    d.get("max_concurrency"),
                    int(d.get("active", True)),
                    int(d.get("trusted_internal_http", False)),
                    int(d.get("security_warning_confirmed", False)),
                    now,
                    now,
                    d.get("api_format", "openai"),
                    d.get("auth_type", "api_key"),
                    d.get("oauth_token_url"),
                    d.get("oauth_client_id"),
                    None,
                    d.get("oauth_scope"),
                ),
            )
            for h in d.get("extra_headers", []):
                c.execute(
                    "INSERT INTO endpoint_headers VALUES (?,?,?,?,?)",
                    (
                        str(uuid4()),
                        rid(x.id),
                        h["name"],
                        None if h.get("secret") else h.get("value"),
                        None,
                    ),
                )
        for x in b.model_profiles:
            d = _model_db(x.data)
            max_tokens_field_path = str(x.data.get("max_tokens_field_path", "max_tokens"))
            c.execute(
                f"{verb} INTO model_profiles VALUES ({','.join('?' for _ in range(26))})",
                (
                    rid(x.id),
                    rid(d.pop("endpoint_id")),
                    *d.values(),
                    now,
                    now,
                    max_tokens_field_path,
                ),
            )
        for x in b.judge_profiles:
            d = x.data
            c.execute(
                f"{verb} INTO judge_profiles("
                "id,name,model_profile_id,prompt_template,template_version,threshold,rubric_json,"
                "temperature,max_output_tokens,is_global_default,active,created_at,updated_at,"
                "temperature_enabled,reasoning_value,implementation_id,implementation_version,"
                "implementation_config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid(x.id),
                    d["name"],
                    rid(d["model_profile_id"]),
                    "preset-v1",
                    "1.0",
                    "0",
                    "[]",
                    str(d.get("temperature") or "0"),
                    d.get("max_output_tokens"),
                    int(d.get("is_global_default", False)),
                    int(d.get("active", True)),
                    now,
                    now,
                    int(d.get("temperature") is not None),
                    d.get("reasoning_value"),
                    d.get("implementation_id", "openai-compatible"),
                    d.get("implementation_version", "1.0"),
                    json.dumps(d.get("implementation_config", {}), separators=(",", ":")),
                ),
            )
        for definition in b.judge_definitions:
            latest = definition.versions[-1] if definition.versions else None
            draft = definition.draft
            if draft is None:
                if latest is None:
                    raise ValueError("judge definition has no content")
                name = latest.data.name
            else:
                name = draft.name
            lifecycle = "published" if definition.versions else "draft"
            c.execute(
                "INSERT INTO judge_definitions("
                "id,name,origin,lifecycle,draft_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    definition.id,
                    name,
                    "local",
                    lifecycle,
                    None if draft is None else draft.model_dump_json(),
                    now,
                    now,
                ),
            )
            for version in definition.versions:
                c.execute(
                    "INSERT INTO judge_definition_versions("
                    "definition_id,version,data_json,semantic_hash,published_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        definition.id,
                        version.version,
                        version.data.model_dump_json(),
                        version.semantic_hash,
                        now,
                    ),
                )
        for x in b.golden_sets:
            d = x.data
            c.execute(
                f"{verb} INTO golden_sets("
                "id,name,description,version,tags_json,active,created_at,updated_at,"
                "judge_profile_id,quality_gates_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rid(x.id),
                    d["name"],
                    d.get("description"),
                    d["version"],
                    json.dumps(d.get("tags", [])),
                    int(d.get("active", True)),
                    now,
                    now,
                    rid(d["judge_profile_id"]) if d.get("judge_profile_id") else None,
                    json.dumps(d.get("quality_gates") or {}),
                ),
            )
        for x in b.golden_set_items:
            d = x.data
            user_prompts = d.get("user_prompts")
            if user_prompts is None:
                user_prompts = [d["prompt"]]
            item_input = GoldenSetItemInput.model_validate(
                {
                    key: value
                    for key, value in d.items()
                    if key not in {"golden_set_id", "prompt", "user_prompts"}
                }
                | {"user_prompts": user_prompts}
            )
            c.execute(
                f"{verb} INTO golden_set_items("
                "id,golden_set_id,case_id,prompt,system_prompt,expected_answer,"
                "evaluation_method,evaluator_config_json,category,tags_json,weight,"
                "enabled,sort_order,created_at,updated_at,user_prompts_json"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid(x.id),
                    rid(d["golden_set_id"]),
                    item_input.case_id,
                    item_input.user_prompts[0],
                    item_input.system_prompt,
                    item_input.expected_answer,
                    item_input.evaluation_method,
                    json.dumps(item_input.evaluator_config, separators=(",", ":")),
                    item_input.category,
                    json.dumps(item_input.tags),
                    str(item_input.weight),
                    int(item_input.enabled),
                    item_input.sort_order,
                    now,
                    now,
                    json.dumps(item_input.user_prompts, ensure_ascii=False),
                ),
            )
        for run in b.runs:
            self._write_run(c, run)

    @staticmethod
    def _write_run(c: sqlite3.Connection, run: RunBundle) -> None:
        def insert(table: str, row: dict[str, Any]) -> None:
            columns = _RUN_TABLE_COLUMNS[table]
            placeholders = ",".join("?" for _ in columns)
            names = ",".join(columns)
            c.execute(
                f"INSERT INTO {table}({names}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )

        insert("runs", run.data)
        for row in run.case_snapshots:
            insert("run_case_snapshots", row)
        for row in run.model_snapshots:
            insert("run_model_snapshots", row)
        for row in run.jobs:
            restored = dict(row)
            restored["lease_owner"] = None
            restored["lease_expires_at"] = None
            insert("run_jobs", restored)
        for row in run.attempts:
            insert("run_attempts", row)
        for row in run.turns:
            insert("run_job_turns", row)
        for row in run.results:
            insert("run_results", row)
        for row in run.quality_gates:
            insert("quality_gate_results", row)


def _model_wire(d: dict[str, Any]) -> dict[str, Any]:
    names = (
        "input_per_million",
        "output_per_million",
        "cached_input_per_million",
        "reasoning_per_million",
        "fixed_per_request",
        "context_threshold",
        "high_context_input_per_million",
        "high_context_output_per_million",
        "high_context_cached_input_per_million",
        "high_context_reasoning_per_million",
    )
    result = dict(d)
    result["pricing"] = {name: result.pop(name) for name in names if name in result}
    return result


def _model_db(d: dict[str, Any]) -> dict[str, Any]:
    x = dict(d)
    order = (
        "model_id",
        "display_name",
        "description",
        "temperature",
        "top_p",
        "max_tokens",
        "reasoning_field_path",
        "reasoning_value",
        "send_reasoning_off",
        "custom_parameters",
        "input_per_million",
        "output_per_million",
        "cached_input_per_million",
        "reasoning_per_million",
        "fixed_per_request",
        "context_threshold",
        "high_context_input_per_million",
        "high_context_output_per_million",
        "high_context_cached_input_per_million",
        "high_context_reasoning_per_million",
        "active",
    )
    return {
        k: (
            json.dumps(x.get(k), separators=(",", ":"))
            if k == "custom_parameters"
            else int(x.get(k, False))
            if k in {"send_reasoning_off", "active"}
            else None
            if x.get(k) is None
            else str(x[k])
            if k not in {"max_tokens", "context_threshold"}
            else x[k]
        )
        for k in ("endpoint_id", *order)
    }
