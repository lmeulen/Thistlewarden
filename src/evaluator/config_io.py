"""Versioned, secret-free functional configuration exchange."""

from __future__ import annotations

import json
import logging
import sqlite3
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

_LOG = logging.getLogger(__name__)


class BundleObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    data: dict[str, Any]


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


class ConfigurationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    endpoints: list[BundleObject] = Field(default_factory=list)
    model_profiles: list[BundleObject] = Field(default_factory=list)
    judge_profiles: list[BundleObject] = Field(default_factory=list)
    judge_definitions: list[JudgeDefinitionBundle] = Field(default_factory=list)
    golden_sets: list[BundleObject] = Field(default_factory=list)
    golden_set_items: list[BundleObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids_and_references(self) -> ConfigurationBundle:
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
        return self


class ImportPlan(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["merge", "replace"]
    creates: int
    updates: int
    archives: int
    uuid_map: dict[str, str]
    conflicts: list[dict[str, str]]
    missing_secrets: list[str]


def _plain(row: sqlite3.Row, excluded: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    result = {
        key: row[key]
        for key in row.keys()
        if key not in excluded | {"id", "created_at", "updated_at"}
    }
    for key in tuple(result):
        if key.endswith("_json") and result[key] is not None:
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
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

    def export(self) -> ConfigurationBundle:
        c = self._factory.connect()
        try:
            endpoints = []
            for row in c.execute("SELECT * FROM endpoints ORDER BY id"):
                data = _plain(row, {"api_key_secret_id"})
                data["api_key"] = None
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
                data["secret_missing"] = row["api_key_secret_id"] is not None
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
            bundle = ConfigurationBundle(
                endpoints=endpoints,
                model_profiles=models,
                judge_profiles=judges,
                judge_definitions=definitions,
                golden_sets=sets,
                golden_set_items=items,
            )
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
                )
            ),
        )
        return bundle

    def parse(self, payload: Any) -> ConfigurationBundle:
        try:
            bundle = ConfigurationBundle.model_validate(payload)
            for item in bundle.endpoints:
                d = dict(item.data)
                d.pop("secret_missing", None)
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
            return bundle
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValidationApiError("Configuration file is invalid.") from exc

    def plan(self, bundle: ConfigurationBundle, mode: Literal["merge", "replace"]) -> ImportPlan:
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
            conflicts.extend(
                {"id": item.id, "resolution": "reject:immutable-definition-exists"}
                for item in bundle.judge_definitions
                if item.id in existing_definitions
            )
            missing = [
                str(x.id)
                for x in bundle.endpoints
                if x.data.get("secret_missing")
                or any(h.get("secret") for h in x.data.get("extra_headers", []))
            ]
            archives = 0
            if mode == "replace":
                archives = sum(
                    c.execute(f"SELECT COUNT(*) FROM {t} WHERE active=1").fetchone()[0]
                    for t in ("endpoints", "model_profiles", "judge_profiles", "golden_sets")
                )
            return ImportPlan(
                mode=mode,
                creates=len(incoming) + len(bundle.judge_definitions),
                updates=0,
                archives=archives,
                uuid_map=mapping,
                conflicts=conflicts,
                missing_secrets=missing,
            )
        finally:
            c.close()

    def import_bundle(
        self, payload: Any, mode: Literal["merge", "replace"], *, preview: bool
    ) -> ImportPlan:
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
            conflict["resolution"] == "reject:immutable-definition-exists"
            for conflict in plan.conflicts
        ):
            raise ConflictError("Judge definition already exists and is immutable.")
        c = self._factory.connect()
        now = to_utc_iso(utc_now())
        try:
            with transaction(c):
                if mode == "replace":
                    suffix = f" [archived {now}]"
                    c.execute(
                        "UPDATE golden_sets SET active=0,name=name||?,updated_at=? WHERE active=1",
                        (suffix, now),
                    )
                    c.execute(
                        "UPDATE judge_profiles SET active=0,is_global_default=0,name=name||?,"
                        "updated_at=? WHERE active=1",
                        (suffix, now),
                    )
                    c.execute(
                        "UPDATE model_profiles SET active=0,display_name=display_name||?,"
                        "updated_at=? WHERE active=1",
                        (suffix, now),
                    )
                    c.execute(
                        "UPDATE endpoints SET active=0,name=name||?,updated_at=? WHERE active=1",
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
                "security_warning_confirmed,created_at,updated_at,api_format"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
