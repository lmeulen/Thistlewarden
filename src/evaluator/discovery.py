"""Authenticated OpenAI model discovery and selective profile import."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.api.errors import InvalidProviderResponseError, ValidationApiError
from evaluator.endpoints import EndpointRecord, SQLiteEndpointRepository
from evaluator.models import (
    ModelProfileInput,
    ModelProfileRecord,
    SQLiteModelProfileRepository,
)
from evaluator.network_security import validate_destination
from evaluator.provider import OpenAIProviderClient, ProviderHeaderResolver
from evaluator.rate_limits import ProviderCallExecutor
from evaluator.time import utc_now


def suggest_models_url(completions_url: str) -> str | None:
    parsed = urlsplit(completions_url)
    path = parsed.path
    for suffix in ("/chat/completions", "/completions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)] + "/models"
            return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    return None


def parse_models_response(content: bytes) -> list[str]:
    try:
        payload = json.loads(content)
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError
        result: list[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise TypeError
            model_id = item["id"].strip()
            if not model_id or len(model_id) > 500:
                raise ValueError
            if model_id not in seen:
                seen.add(model_id)
                result.append(model_id)
        return result
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidProviderResponseError(
            "Providerresponse heeft geen geldig modelcontract."
        ) from exc


class DiscoveryImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(min_length=1, max_length=1000)


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    model_id: str
    suggested_display_name: str


class ModelDiscoveryClient:
    def __init__(
        self,
        headers: ProviderHeaderResolver,
        *,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._headers = headers
        self._limit = max_response_bytes
        self._transport = transport

    def discover(self, endpoint: EndpointRecord) -> list[str]:
        if endpoint.data.models_url is None:
            raise ValidationApiError("Models-URL is niet geconfigureerd.")
        if self._transport is None:
            validate_destination(endpoint, endpoint.data.models_url)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(float(endpoint.data.timeout_seconds)),
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                with client.stream(
                    "GET", endpoint.data.models_url, headers=self._headers.resolve(endpoint)
                ) as response:
                    OpenAIProviderClient._classify_status(response)
                    content = self._read_limited(response)
        except httpx.TimeoutException as exc:
            from evaluator.api.errors import TimeoutError

            raise TimeoutError() from exc
        except httpx.NetworkError as exc:
            from evaluator.api.errors import NetworkError

            raise NetworkError() from exc
        except httpx.HTTPError as exc:
            from evaluator.api.errors import NetworkError

            raise NetworkError() from exc
        return parse_models_response(content)

    def _read_limited(self, response: httpx.Response) -> bytes:
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > self._limit:
                raise InvalidProviderResponseError("Model response exceeds maximum size.")
        return bytes(content)


class ModelDiscoveryService:
    def __init__(
        self,
        endpoints: SQLiteEndpointRepository,
        models: SQLiteModelProfileRepository,
        client: ModelDiscoveryClient,
        executor: ProviderCallExecutor,
    ) -> None:
        self._endpoints = endpoints
        self._models = models
        self._client = client
        self._executor = executor

    def discover(self, endpoint_id: Any) -> list[DiscoveryCandidate]:
        endpoint = self._get_endpoint(endpoint_id)
        model_ids = self._executor.execute(endpoint, lambda: self._client.discover(endpoint))
        existing = {
            record.data.display_name.casefold()
            for record in self._models.list_for_endpoint(endpoint.id)
        }
        candidates: list[DiscoveryCandidate] = []
        reserved = set(existing)
        for model_id in model_ids:
            name = _unique_name(model_id, reserved)
            reserved.add(name.casefold())
            candidates.append(DiscoveryCandidate(model_id, name))
        return candidates

    def import_selected(self, endpoint_id: Any, payload: Any) -> list[ModelProfileRecord]:
        try:
            selection = DiscoveryImportInput.model_validate(payload)
        except ValidationError as exc:
            raise ValidationApiError.from_pydantic(exc) from exc
        if len(set(selection.model_ids)) != len(selection.model_ids):
            raise ValidationApiError("model_ids moeten uniek zijn.")
        endpoint = self._get_endpoint(endpoint_id)
        discovered = self._executor.execute(endpoint, lambda: self._client.discover(endpoint))
        available = set(discovered)
        if any(model_id not in available for model_id in selection.model_ids):
            raise ValidationApiError("Selectie bevat model-ID dat niet is ontdekt.")
        existing = {
            record.data.display_name.casefold()
            for record in self._models.list_for_endpoint(endpoint.id)
        }
        now = utc_now()
        records: list[ModelProfileRecord] = []
        for model_id in selection.model_ids:
            name = _unique_name(model_id, existing)
            existing.add(name.casefold())
            records.append(
                ModelProfileRecord(
                    uuid4(),
                    endpoint.id,
                    ModelProfileInput(model_id=model_id, display_name=name),
                    now,
                    now,
                )
            )
        try:
            self._models.save_many(records)
        except sqlite3.IntegrityError as exc:
            from evaluator.api.errors import ConflictError

            raise ConflictError("Modelimport conflicteert met bestaande profielen.") from exc
        return [record for item in records if (record := self._models.get(item.id)) is not None]

    def _get_endpoint(self, endpoint_id: Any) -> EndpointRecord:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None:
            from evaluator.api.errors import NotFoundError

            raise NotFoundError("Endpoint not found.")
        return endpoint


def _unique_name(base: str, reserved: set[str]) -> str:
    if base.casefold() not in reserved:
        return base
    index = 2
    while True:
        suffix = f" ({index})"
        candidate = base[: 200 - len(suffix)] + suffix
        if candidate.casefold() not in reserved:
            return candidate
        index += 1
