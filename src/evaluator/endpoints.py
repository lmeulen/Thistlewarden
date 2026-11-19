"""Provider endpoint domain models, validation, persistence, and service."""

from __future__ import annotations

import ipaddress
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evaluator.api.errors import ConflictError, NotFoundError, ValidationApiError
from evaluator.database import SQLiteConnectionFactory, transaction
from evaluator.logging_config import log_semantic_event
from evaluator.secrets import SQLiteSecretRepository
from evaluator.time import to_utc_iso, utc_now

_LOG = logging.getLogger(__name__)
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MASK = "********"


def validate_header_name(value: str) -> str:
    if not _HEADER_NAME.fullmatch(value):
        raise ValueError("must be a valid HTTP header name")
    return value


def validate_provider_url(
    value: str | None, *, trusted_internal: bool, confirmed: bool
) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("must not contain credentials")
    if parsed.fragment:
        raise ValueError("must not contain a fragment")
    if parsed.scheme == "https":
        return value
    hostname = parsed.hostname.lower()
    local = hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if not local and not (trusted_internal and confirmed):
        raise ValueError(
            "external HTTP requires trusted_internal_http and security_warning_confirmed"
        )
    return value


class HeaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=16_384)
    secret: bool = False

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return validate_header_name(value)


EndpointApiFormat = Literal["openai", "anthropic"]


class EndpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    api_format: EndpointApiFormat = "openai"
    completions_url: str
    models_url: str | None = None
    api_key: str | None = Field(default=None, max_length=16_384)
    api_key_header: str = Field(default="Authorization", max_length=256)
    api_key_prefix: str = Field(default="", max_length=100)
    extra_headers: list[HeaderInput] = Field(default_factory=list, max_length=100)
    timeout_seconds: int = Field(default=60, gt=0, le=3600)
    max_retries: int = Field(default=2, ge=0, le=20)
    requests_per_minute: int | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, gt=0)
    active: bool = True
    trusted_internal_http: bool = False
    security_warning_confirmed: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("api_key_header")
    @classmethod
    def valid_api_header(cls, value: str) -> str:
        return validate_header_name(value)

    @model_validator(mode="after")
    def validate_urls_and_headers(self) -> EndpointInput:
        self.completions_url = (
            validate_provider_url(
                self.completions_url,
                trusted_internal=self.trusted_internal_http,
                confirmed=self.security_warning_confirmed,
            )
            or ""
        )
        self.models_url = validate_provider_url(
            self.models_url,
            trusted_internal=self.trusted_internal_http,
            confirmed=self.security_warning_confirmed,
        )
        lowered = [header.name.lower() for header in self.extra_headers]
        if len(lowered) != len(set(lowered)):
            raise ValueError("extra header names must be unique")
        if self.api_key_header.lower() in lowered:
            raise ValueError("API key header must not duplicate an extra header")
        return self


class EndpointPatch(BaseModel):
    """Patch payload; omitted fields preserve values and blank secrets preserve keys."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    api_format: EndpointApiFormat | None = None
    completions_url: str | None = None
    models_url: str | None = None
    api_key: str | None = Field(default=None, max_length=16_384)
    api_key_header: str | None = Field(default=None, max_length=256)
    api_key_prefix: str | None = Field(default=None, max_length=100)
    extra_headers: list[HeaderInput] | None = Field(default=None, max_length=100)
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=20)
    requests_per_minute: int | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, gt=0)
    active: bool | None = None
    trusted_internal_http: bool | None = None
    security_warning_confirmed: bool | None = None


@dataclass(frozen=True, slots=True)
class EndpointRecord:
    id: UUID
    data: EndpointInput
    api_key_secret_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @property
    def has_api_key(self) -> bool:
        """Report key presence without exposing secret material or its identifier."""

        return self.api_key_secret_id is not None


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SQLiteEndpointRepository:
    def __init__(self, factory: SQLiteConnectionFactory) -> None:
        self._factory = factory

    def list_all(self) -> list[EndpointRecord]:
        connection = self._factory.connect()
        try:
            ids = [
                UUID(row[0]) for row in connection.execute("SELECT id FROM endpoints ORDER BY name")
            ]
        finally:
            connection.close()
        return [record for endpoint_id in ids if (record := self.get(endpoint_id)) is not None]

    def get(self, endpoint_id: UUID) -> EndpointRecord | None:
        connection = self._factory.connect()
        try:
            row = connection.execute(
                "SELECT * FROM endpoints WHERE id = ?", (str(endpoint_id),)
            ).fetchone()
            if row is None:
                return None
            header_rows = connection.execute(
                """
                SELECT name, value, secret_id FROM endpoint_headers
                WHERE endpoint_id = ? ORDER BY name
                """,
                (str(endpoint_id),),
            ).fetchall()
        finally:
            connection.close()
        headers = [
            HeaderInput(
                name=item["name"],
                value=_MASK if item["secret_id"] else item["value"],
                secret=item["secret_id"] is not None,
            )
            for item in header_rows
        ]
        data = EndpointInput(
            name=row["name"],
            api_format=row["api_format"],
            completions_url=row["completions_url"],
            models_url=row["models_url"],
            api_key=_MASK if row["api_key_secret_id"] else None,
            api_key_header=row["api_key_header"],
            api_key_prefix=row["api_key_prefix"],
            extra_headers=headers,
            timeout_seconds=row["timeout_seconds"],
            max_retries=row["max_retries"],
            requests_per_minute=row["requests_per_minute"],
            tokens_per_minute=row["tokens_per_minute"],
            max_concurrency=row["max_concurrency"],
            active=bool(row["active"]),
            trusted_internal_http=bool(row["trusted_internal_http"]),
            security_warning_confirmed=bool(row["security_warning_confirmed"]),
        )
        secret_id = UUID(row["api_key_secret_id"]) if row["api_key_secret_id"] else None
        return EndpointRecord(
            UUID(row["id"]),
            data,
            secret_id,
            _parse_utc(row["created_at"]),
            _parse_utc(row["updated_at"]),
        )

    def insert(
        self,
        endpoint_id: UUID,
        data: EndpointInput,
        api_key_secret_id: UUID | None,
        headers: list[tuple[HeaderInput, UUID | None]],
    ) -> None:
        now = to_utc_iso(utc_now())
        connection = self._factory.connect()
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO endpoints(
                        id, name, api_format, completions_url, models_url, api_key_secret_id,
                        api_key_header, api_key_prefix, timeout_seconds, max_retries,
                        requests_per_minute, tokens_per_minute, max_concurrency, active,
                        trusted_internal_http, security_warning_confirmed, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(endpoint_id),
                        data.name,
                        data.api_format,
                        data.completions_url,
                        data.models_url,
                        str(api_key_secret_id) if api_key_secret_id else None,
                        data.api_key_header,
                        data.api_key_prefix,
                        data.timeout_seconds,
                        data.max_retries,
                        data.requests_per_minute,
                        data.tokens_per_minute,
                        data.max_concurrency,
                        int(data.active),
                        int(data.trusted_internal_http),
                        int(data.security_warning_confirmed),
                        now,
                        now,
                    ),
                )
                for header, secret_id in headers:
                    connection.execute(
                        """
                        INSERT INTO endpoint_headers(id, endpoint_id, name, value, secret_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            str(endpoint_id),
                            header.name,
                            None if secret_id else header.value,
                            str(secret_id) if secret_id else None,
                        ),
                    )
        finally:
            connection.close()

    def replace(
        self,
        endpoint_id: UUID,
        data: EndpointInput,
        api_key_secret_id: UUID | None,
        headers: list[tuple[HeaderInput, UUID | None]],
    ) -> None:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE endpoints SET
                        name = ?, api_format = ?, completions_url = ?, models_url = ?,
                        api_key_secret_id = ?, api_key_header = ?, api_key_prefix = ?,
                        timeout_seconds = ?,
                        max_retries = ?, requests_per_minute = ?, tokens_per_minute = ?,
                        max_concurrency = ?, active = ?, trusted_internal_http = ?,
                        security_warning_confirmed = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        data.name,
                        data.api_format,
                        data.completions_url,
                        data.models_url,
                        str(api_key_secret_id) if api_key_secret_id else None,
                        data.api_key_header,
                        data.api_key_prefix,
                        data.timeout_seconds,
                        data.max_retries,
                        data.requests_per_minute,
                        data.tokens_per_minute,
                        data.max_concurrency,
                        int(data.active),
                        int(data.trusted_internal_http),
                        int(data.security_warning_confirmed),
                        to_utc_iso(utc_now()),
                        str(endpoint_id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(endpoint_id)
                connection.execute(
                    "DELETE FROM endpoint_headers WHERE endpoint_id = ?", (str(endpoint_id),)
                )
                for header, secret_id in headers:
                    connection.execute(
                        """
                        INSERT INTO endpoint_headers(id, endpoint_id, name, value, secret_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            str(endpoint_id),
                            header.name,
                            None if secret_id else header.value,
                            str(secret_id) if secret_id else None,
                        ),
                    )
        finally:
            connection.close()

    def header_secret_ids(self, endpoint_id: UUID) -> dict[str, UUID]:
        connection = self._factory.connect()
        try:
            rows = connection.execute(
                """
                SELECT name, secret_id FROM endpoint_headers
                WHERE endpoint_id = ? AND secret_id IS NOT NULL
                """,
                (str(endpoint_id),),
            ).fetchall()
            return {str(row["name"]).lower(): UUID(row["secret_id"]) for row in rows}
        finally:
            connection.close()

    def delete(self, endpoint_id: UUID, *, cascade_models: bool = False) -> bool:
        connection = self._factory.connect()
        try:
            with transaction(connection):
                if cascade_models:
                    params = (str(endpoint_id),)
                    connection.execute(
                        """
                        UPDATE golden_sets SET judge_profile_id = NULL
                        WHERE judge_profile_id IN (
                            SELECT judge_profiles.id FROM judge_profiles
                            JOIN model_profiles
                              ON model_profiles.id = judge_profiles.model_profile_id
                            WHERE model_profiles.endpoint_id = ?
                        )
                        """,
                        params,
                    )
                    connection.execute(
                        """
                        DELETE FROM judge_profiles WHERE model_profile_id IN (
                            SELECT id FROM model_profiles WHERE endpoint_id = ?
                        )
                        """,
                        params,
                    )
                    connection.execute("DELETE FROM model_profiles WHERE endpoint_id = ?", params)
                cursor = connection.execute(
                    "DELETE FROM endpoints WHERE id = ?", (str(endpoint_id),)
                )
            return cursor.rowcount == 1
        finally:
            connection.close()


class EndpointService:
    def __init__(
        self, repository: SQLiteEndpointRepository, secrets: SQLiteSecretRepository
    ) -> None:
        self._repository = repository
        self._secrets = secrets

    @staticmethod
    def parse_input(payload: Any) -> EndpointInput:
        try:
            return EndpointInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc

    @staticmethod
    def parse_patch(payload: Any) -> EndpointPatch:
        try:
            return EndpointPatch.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc

    def list_all(self) -> list[EndpointRecord]:
        return self._repository.list_all()

    def get(self, endpoint_id: UUID) -> EndpointRecord:
        record = self._repository.get(endpoint_id)
        if record is None:
            raise NotFoundError("Endpoint not found.")
        return record

    def _secret_headers(
        self, headers: list[HeaderInput]
    ) -> tuple[list[tuple[HeaderInput, UUID | None]], list[UUID]]:
        created: list[UUID] = []
        result: list[tuple[HeaderInput, UUID | None]] = []
        for header in headers:
            secret_id = (
                self._secrets.create("endpoint_header", header.value) if header.secret else None
            )
            if secret_id:
                created.append(secret_id)
            result.append((header, secret_id))
        return result, created

    def create(self, payload: Any) -> EndpointRecord:
        data = self.parse_input(payload)
        created: list[UUID] = []
        try:
            api_secret = (
                self._secrets.create("endpoint_api_key", data.api_key) if data.api_key else None
            )
            if api_secret:
                created.append(api_secret)
            headers, header_secrets = self._secret_headers(data.extra_headers)
            created.extend(header_secrets)
            endpoint_id = uuid4()
            self._repository.insert(endpoint_id, data, api_secret, headers)
            record = self.get(endpoint_id)
            log_semantic_event(_LOG, "endpoint_created", endpoint_id=endpoint_id)
            return record
        except sqlite3.IntegrityError as exc:
            for secret_id in created:
                self._secrets.delete(secret_id)
            raise ConflictError("Endpoint name or header name already exists.") from exc
        except BaseException:
            for secret_id in created:
                self._secrets.delete(secret_id)
            raise

    def update(self, endpoint_id: UUID, payload: Any) -> EndpointRecord:
        existing = self.get(endpoint_id)
        patch = self.parse_patch(payload)
        changes = patch.model_dump(exclude_unset=True)
        incoming_api_key = changes.pop("api_key", None)
        incoming_headers = changes.pop("extra_headers", None)
        merged = existing.data.model_dump()
        merged.update(changes)
        if incoming_api_key in {None, ""}:
            merged["api_key"] = _MASK if existing.api_key_secret_id else None
        else:
            merged["api_key"] = incoming_api_key
        if incoming_headers is not None:
            merged["extra_headers"] = incoming_headers
        data = self.parse_input(merged)

        new_secrets: list[UUID] = []
        old_secret_ids: list[UUID] = []
        api_secret = existing.api_key_secret_id
        try:
            if incoming_api_key not in {None, ""}:
                api_secret = self._secrets.create("endpoint_api_key", incoming_api_key)
                new_secrets.append(api_secret)
                if existing.api_key_secret_id:
                    old_secret_ids.append(existing.api_key_secret_id)
            if incoming_headers is None:
                headers: list[tuple[HeaderInput, UUID | None]] = []
                for header in existing.data.extra_headers:
                    if header.secret:
                        # Preserve existing secret reference by case-insensitive header name.
                        secret_ids = self._repository.header_secret_ids(endpoint_id)
                        headers.append((header, secret_ids[header.name.lower()]))
                    else:
                        headers.append((header, None))
            else:
                existing_header_secrets = self._repository.header_secret_ids(endpoint_id)
                headers = []
                reused: set[UUID] = set()
                for header in data.extra_headers:
                    old_id = existing_header_secrets.get(header.name.lower())
                    if header.secret and header.value == "":
                        if old_id is None:
                            raise ValidationApiError(
                                f"Secret header {header.name} requires a value."
                            )
                        headers.append((header, old_id))
                        reused.add(old_id)
                    elif header.secret:
                        secret_id = self._secrets.create("endpoint_header", header.value)
                        new_secrets.append(secret_id)
                        headers.append((header, secret_id))
                    else:
                        headers.append((header, None))
                old_secret_ids.extend(
                    secret_id
                    for secret_id in existing_header_secrets.values()
                    if secret_id not in reused
                )
            self._repository.replace(endpoint_id, data, api_secret, headers)
        except sqlite3.IntegrityError as exc:
            for secret_id in new_secrets:
                self._secrets.delete(secret_id)
            raise ConflictError("Endpoint name or header name already exists.") from exc
        except BaseException:
            for secret_id in new_secrets:
                self._secrets.delete(secret_id)
            raise
        for secret_id in old_secret_ids:
            self._secrets.delete(secret_id)
        record = self.get(endpoint_id)
        log_semantic_event(_LOG, "endpoint_updated", endpoint_id=endpoint_id)
        return record

    def delete(self, endpoint_id: UUID, *, cascade_models: bool = False) -> None:
        existing = self.get(endpoint_id)
        header_secret_ids = list(self._repository.header_secret_ids(endpoint_id).values())
        try:
            if not self._repository.delete(endpoint_id, cascade_models=cascade_models):
                raise NotFoundError("Endpoint not found.")
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "Endpoint or its model profiles have historical references and cannot be deleted."
            ) from exc
        if existing.api_key_secret_id:
            self._secrets.delete(existing.api_key_secret_id)
        for secret_id in header_secret_ids:
            self._secrets.delete(secret_id)
        log_semantic_event(_LOG, "endpoint_deleted", endpoint_id=endpoint_id)


def endpoint_to_dict(record: EndpointRecord) -> dict[str, Any]:
    result = record.data.model_dump(mode="json")
    result.update(
        {
            "id": str(record.id),
            "created_at": to_utc_iso(record.created_at),
            "updated_at": to_utc_iso(record.updated_at),
        }
    )
    return result


def compose_api_key(prefix: str, key: str) -> str:
    """Combine prefix exactly as configured, without trimming significant spaces."""

    return f"{prefix}{key}"
