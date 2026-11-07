"""Model test profiles, pricing, inference composition, persistence, and service."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_semantic_event
from evaluator.time import to_utc_iso, utc_now

_LOG = logging.getLogger(__name__)
_PATH_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_PROTECTED_FIELDS = frozenset(
    {"model", "messages", "stream", "stream_options", "temperature", "top_p", "max_tokens"}
)


class PricingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    output_per_million: Decimal = Field(default=Decimal("0"), ge=0)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    reasoning_per_million: Decimal | None = Field(default=None, ge=0)
    fixed_per_request: Decimal | None = Field(default=None, ge=0)
    context_threshold: int | None = Field(default=None, gt=0)
    high_context_input_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_output_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    high_context_reasoning_per_million: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_high_context_group(self) -> PricingInput:
        required = (
            self.context_threshold,
            self.high_context_input_per_million,
            self.high_context_output_per_million,
        )
        if any(value is not None for value in required) and not all(
            value is not None for value in required
        ):
            raise ValueError(
                "context_threshold and both high-context base prices must be configured together"
            )
        if self.context_threshold is None and (
            self.high_context_cached_input_per_million is not None
            or self.high_context_reasoning_per_million is not None
        ):
            raise ValueError("high-context special prices require a context threshold")
        return self

    def effective_rates(self, input_tokens: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Return input, output, cached-input, and reasoning rates for request context."""

        high = self.context_threshold is not None and input_tokens > self.context_threshold
        if high:
            assert self.high_context_input_per_million is not None
            assert self.high_context_output_per_million is not None
            input_rate = self.high_context_input_per_million
            output_rate = self.high_context_output_per_million
            cached_rate = (
                self.high_context_cached_input_per_million
                if self.high_context_cached_input_per_million is not None
                else input_rate
            )
            reasoning_rate = (
                self.high_context_reasoning_per_million
                if self.high_context_reasoning_per_million is not None
                else output_rate
            )
            return input_rate, output_rate, cached_rate, reasoning_rate
        return (
            self.input_per_million,
            self.output_per_million,
            self.cached_input_per_million
            if self.cached_input_per_million is not None
            else self.input_per_million,
            self.reasoning_per_million
            if self.reasoning_per_million is not None
            else self.output_per_million,
        )


class ModelProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    top_p: Decimal | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    max_tokens_field_path: str = Field(default="max_tokens", min_length=1, max_length=500)
    reasoning_field_path: str | None = Field(default=None, max_length=500)
    reasoning_value: str | None = Field(default=None, max_length=500)
    send_reasoning_off: bool = False
    custom_parameters: dict[str, Any] = Field(default_factory=dict)
    pricing: PricingInput = Field(default_factory=PricingInput)
    active: bool = True

    @field_validator("model_id", "display_name")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_reasoning_and_custom(self) -> ModelProfileInput:
        if (self.reasoning_field_path is None) != (self.reasoning_value is None):
            raise ValueError("reasoning_field_path and reasoning_value must be configured together")
        binding_paths = [self.max_tokens_field_path]
        if self.reasoning_field_path is not None:
            binding_paths.append(self.reasoning_field_path)
        for path in binding_paths:
            parts = path.split(".")
            if any(not _PATH_PART.fullmatch(part) for part in parts):
                raise ValueError("parameter field path contains an invalid segment")
            if parts[0] in {"model", "messages", "stream", "stream_options"}:
                raise ValueError("parameter field path conflicts with a protected request field")
        if self.reasoning_field_path == self.max_tokens_field_path:
            raise ValueError("parameter field paths must be unique")
        protected = _PROTECTED_FIELDS & self.custom_parameters.keys()
        protected |= {path.split(".")[0] for path in binding_paths} & self.custom_parameters.keys()
        if protected:
            raise ValueError(f"custom_parameters contains protected fields: {sorted(protected)}")
        try:
            json.dumps(self.custom_parameters, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("custom_parameters must be finite JSON data") from exc
        return self


class ModelProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=500)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    top_p: Decimal | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    max_tokens_field_path: str | None = Field(default=None, min_length=1, max_length=500)
    reasoning_field_path: str | None = Field(default=None, max_length=500)
    reasoning_value: str | None = Field(default=None, max_length=500)
    send_reasoning_off: bool | None = None
    custom_parameters: dict[str, Any] | None = None
    pricing: PricingInput | None = None
    active: bool | None = None


@dataclass(frozen=True, slots=True)
class ModelProfileRecord:
    id: UUID
    endpoint_id: UUID
    data: ModelProfileInput
    created_at: datetime
    updated_at: datetime


def _nested_set(target: dict[str, Any], path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            nested: dict[str, Any] = {}
            current[part] = nested
            current = nested
        elif isinstance(existing, dict):
            current = existing
        else:
            raise ValueError(f"parameter path conflicts at {part}")
    current[parts[-1]] = value


def compose_inference_parameters(profile: ModelProfileInput) -> dict[str, Any]:
    """Compose optional provider fields without nulls or protected custom overrides."""

    result = dict(profile.custom_parameters)
    if profile.temperature is not None:
        result["temperature"] = profile.temperature
    if profile.top_p is not None:
        result["top_p"] = profile.top_p
    if profile.max_tokens is not None:
        _nested_set(result, profile.max_tokens_field_path, profile.max_tokens)
    if profile.reasoning_field_path and profile.reasoning_value:
        if profile.reasoning_value != "off" or profile.send_reasoning_off:
            _nested_set(result, profile.reasoning_field_path, profile.reasoning_value)
    return result


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class SQLiteModelProfileRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def get(self, profile_id: UUID) -> ModelProfileRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE id = ?", (str(profile_id),)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else self._map(row)

    def list_for_endpoint(self, endpoint_id: UUID) -> list[ModelProfileRecord]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM model_profiles WHERE endpoint_id = ? ORDER BY display_name",
                (str(endpoint_id),),
            ).fetchall()
        finally:
            connection.close()
        return [self._map(row) for row in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> ModelProfileRecord:
        pricing = PricingInput(
            input_per_million=Decimal(row["input_per_million"]),
            output_per_million=Decimal(row["output_per_million"]),
            cached_input_per_million=Decimal(row["cached_input_per_million"])
            if row["cached_input_per_million"] is not None
            else None,
            reasoning_per_million=Decimal(row["reasoning_per_million"])
            if row["reasoning_per_million"] is not None
            else None,
            fixed_per_request=Decimal(row["fixed_per_request"])
            if row["fixed_per_request"] is not None
            else None,
            context_threshold=row["context_threshold"],
            high_context_input_per_million=Decimal(row["high_context_input_per_million"])
            if row["high_context_input_per_million"] is not None
            else None,
            high_context_output_per_million=Decimal(row["high_context_output_per_million"])
            if row["high_context_output_per_million"] is not None
            else None,
            high_context_cached_input_per_million=Decimal(
                row["high_context_cached_input_per_million"]
            )
            if row["high_context_cached_input_per_million"] is not None
            else None,
            high_context_reasoning_per_million=Decimal(row["high_context_reasoning_per_million"])
            if row["high_context_reasoning_per_million"] is not None
            else None,
        )
        data = ModelProfileInput(
            model_id=row["model_id"],
            display_name=row["display_name"],
            description=row["description"],
            temperature=Decimal(row["temperature"]) if row["temperature"] is not None else None,
            top_p=Decimal(row["top_p"]) if row["top_p"] is not None else None,
            max_tokens=row["max_tokens"],
            max_tokens_field_path=row["max_tokens_field_path"],
            reasoning_field_path=row["reasoning_field_path"],
            reasoning_value=row["reasoning_value"],
            send_reasoning_off=bool(row["send_reasoning_off"]),
            custom_parameters=json.loads(row["custom_parameters_json"]),
            pricing=pricing,
            active=bool(row["active"]),
        )
        return ModelProfileRecord(
            UUID(row["id"]),
            UUID(row["endpoint_id"]),
            data,
            _parse_utc(row["created_at"]),
            _parse_utc(row["updated_at"]),
        )

    def save(self, record: ModelProfileRecord) -> None:
        data, pricing = record.data, record.data.pricing
        values = (
            str(record.id),
            str(record.endpoint_id),
            data.model_id,
            data.display_name,
            data.description,
            _decimal_text(data.temperature),
            _decimal_text(data.top_p),
            data.max_tokens,
            data.reasoning_field_path,
            data.reasoning_value,
            int(data.send_reasoning_off),
            json.dumps(
                data.custom_parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
            _decimal_text(pricing.input_per_million),
            _decimal_text(pricing.output_per_million),
            _decimal_text(pricing.cached_input_per_million),
            _decimal_text(pricing.reasoning_per_million),
            _decimal_text(pricing.fixed_per_request),
            pricing.context_threshold,
            _decimal_text(pricing.high_context_input_per_million),
            _decimal_text(pricing.high_context_output_per_million),
            _decimal_text(pricing.high_context_cached_input_per_million),
            _decimal_text(pricing.high_context_reasoning_per_million),
            int(data.active),
            to_utc_iso(record.created_at),
            to_utc_iso(record.updated_at),
            data.max_tokens_field_path,
        )
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """INSERT INTO model_profiles VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET model_id=excluded.model_id,
                    display_name=excluded.display_name,description=excluded.description,
                    temperature=excluded.temperature,top_p=excluded.top_p,max_tokens=excluded.max_tokens,
                    reasoning_field_path=excluded.reasoning_field_path,reasoning_value=excluded.reasoning_value,
                    send_reasoning_off=excluded.send_reasoning_off,
                    custom_parameters_json=excluded.custom_parameters_json,
                    input_per_million=excluded.input_per_million,
                    output_per_million=excluded.output_per_million,
                    cached_input_per_million=excluded.cached_input_per_million,
                    reasoning_per_million=excluded.reasoning_per_million,
                    fixed_per_request=excluded.fixed_per_request,
                    context_threshold=excluded.context_threshold,
                    high_context_input_per_million=excluded.high_context_input_per_million,
                    high_context_output_per_million=excluded.high_context_output_per_million,
                    high_context_cached_input_per_million=excluded.high_context_cached_input_per_million,
                    high_context_reasoning_per_million=excluded.high_context_reasoning_per_million,
                    active=excluded.active,updated_at=excluded.updated_at,
                    max_tokens_field_path=excluded.max_tokens_field_path""",
                    values,
                )
        finally:
            connection.close()

    def save_many(self, records: list[ModelProfileRecord]) -> None:
        if not records:
            return
        connection = self._factory.connect()
        try:
            with transaction(connection):
                for record in records:
                    values = self._values(record)
                    connection.execute(
                        "INSERT INTO model_profiles VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        values,
                    )
        finally:
            connection.close()

    @staticmethod
    def _values(record: ModelProfileRecord) -> tuple[Any, ...]:
        data, pricing = record.data, record.data.pricing
        return (
            str(record.id),
            str(record.endpoint_id),
            data.model_id,
            data.display_name,
            data.description,
            _decimal_text(data.temperature),
            _decimal_text(data.top_p),
            data.max_tokens,
            data.reasoning_field_path,
            data.reasoning_value,
            int(data.send_reasoning_off),
            json.dumps(
                data.custom_parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
            _decimal_text(pricing.input_per_million),
            _decimal_text(pricing.output_per_million),
            _decimal_text(pricing.cached_input_per_million),
            _decimal_text(pricing.reasoning_per_million),
            _decimal_text(pricing.fixed_per_request),
            pricing.context_threshold,
            _decimal_text(pricing.high_context_input_per_million),
            _decimal_text(pricing.high_context_output_per_million),
            _decimal_text(pricing.high_context_cached_input_per_million),
            _decimal_text(pricing.high_context_reasoning_per_million),
            int(data.active),
            to_utc_iso(record.created_at),
            to_utc_iso(record.updated_at),
            data.max_tokens_field_path,
        )

    def delete(self, profile_id: UUID) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    "DELETE FROM model_profiles WHERE id = ?", (str(profile_id),)
                )
            return cursor.rowcount == 1
        finally:
            connection.close()


class ModelProfileService:
    def __init__(self, repository: SQLiteModelProfileRepository, endpoint_repository: Any) -> None:
        self._repository = repository
        self._endpoints = endpoint_repository

    @staticmethod
    def _input(payload: Any) -> ModelProfileInput:
        try:
            return ModelProfileInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc

    def list_for_endpoint(self, endpoint_id: UUID) -> list[ModelProfileRecord]:
        if self._endpoints.get(endpoint_id) is None:
            raise NotFoundError("Endpoint not found.")
        return self._repository.list_for_endpoint(endpoint_id)

    def get(self, profile_id: UUID) -> ModelProfileRecord:
        record = self._repository.get(profile_id)
        if record is None:
            raise NotFoundError("Model profile not found.")
        return record

    def create(self, endpoint_id: UUID, payload: Any) -> ModelProfileRecord:
        if self._endpoints.get(endpoint_id) is None:
            raise NotFoundError("Endpoint not found.")
        data = self._input(payload)
        now = utc_now()
        record = ModelProfileRecord(uuid4(), endpoint_id, data, now, now)
        try:
            self._repository.save(record)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Profielnaam bestaat al binnen endpoint.") from exc
        result = self.get(record.id)
        log_semantic_event(
            _LOG,
            "model_profile_created",
            object_id=result.id,
            endpoint_id=result.endpoint_id,
        )
        return result

    def update(self, profile_id: UUID, payload: Any) -> ModelProfileRecord:
        existing = self.get(profile_id)
        try:
            patch = ModelProfilePatch.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        merged = existing.data.model_dump()
        merged.update(patch.model_dump(exclude_unset=True))
        data = self._input(merged)
        updated = ModelProfileRecord(
            existing.id, existing.endpoint_id, data, existing.created_at, utc_now()
        )
        try:
            self._repository.save(updated)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Profielnaam bestaat al binnen endpoint.") from exc
        result = self.get(profile_id)
        log_semantic_event(
            _LOG,
            "model_profile_updated",
            object_id=result.id,
            endpoint_id=result.endpoint_id,
        )
        return result

    def delete(self, profile_id: UUID) -> None:
        existing = self.get(profile_id)
        try:
            if not self._repository.delete(profile_id):
                raise NotFoundError("Model profile not found.")
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Modelprofiel heeft historische referenties.") from exc
        log_semantic_event(
            _LOG,
            "model_profile_deleted",
            object_id=profile_id,
            endpoint_id=existing.endpoint_id,
        )


def profile_to_dict(record: ModelProfileRecord) -> dict[str, Any]:
    result = record.data.model_dump(mode="json")
    result.update(
        {
            "id": str(record.id),
            "endpoint_id": str(record.endpoint_id),
            "created_at": to_utc_iso(record.created_at),
            "updated_at": to_utc_iso(record.updated_at),
        }
    )
    return result
