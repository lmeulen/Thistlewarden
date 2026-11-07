"""Safe OpenAI-compatible non-streaming request construction and HTTP client."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import simplejson
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evaluator.api.errors import (
    AuthenticationError,
    InvalidProviderResponseError,
    NetworkError,
    ProviderClientError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
)
from evaluator.endpoints import EndpointRecord, SQLiteEndpointRepository, compose_api_key
from evaluator.models import ModelProfileInput, compose_inference_parameters
from evaluator.network_security import validate_destination
from evaluator.secrets import SQLiteSecretRepository

_ENV_REFERENCE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_REQUEST_ID_HEADERS = ("x-request-id", "request-id", "x-ms-request-id")
_DIAGNOSTIC_BODY_LIMIT = 32 * 1024


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    status_code: int
    provider_code: str | None
    error_type: str | None
    parameter: str | None
    message: str | None
    provider_request_id: str | None
    raw_response: str | None
    response_truncated: bool


def _redact_diagnostic(value: str | None, secrets: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def attach_provider_diagnostic(
    error: Exception,
    response: httpx.Response,
    *,
    redact_values: tuple[str, ...],
    limit: int = _DIAGNOSTIC_BODY_LIMIT,
) -> None:
    """Attach bounded transient diagnostics; caller must never log or persist raw response."""

    content_type = response.headers.get("content-type", "").lower()
    textual = not content_type or "json" in content_type or content_type.startswith("text/")
    body = bytearray()
    truncated = False
    if textual:
        chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
        for chunk in chunks:
            remaining = limit - len(body)
            if len(chunk) > remaining:
                body.extend(chunk[:remaining])
                truncated = True
                break
            body.extend(chunk)
    raw = (
        _redact_diagnostic(body.decode("utf-8", errors="replace"), redact_values) if body else None
    )
    provider_code = error_type = parameter = message = None
    if body:
        try:
            payload = json.loads(body)
            detail = payload.get("error", payload) if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                provider_code = _diagnostic_scalar(detail.get("code"), redact_values)
                error_type = _diagnostic_scalar(detail.get("type"), redact_values)
                parameter = _diagnostic_scalar(detail.get("param"), redact_values)
                message = _diagnostic_scalar(detail.get("message"), redact_values)
        except json.JSONDecodeError:
            pass
    lowered = {key.lower(): value for key, value in response.headers.items()}
    request_id = next((lowered[name] for name in _REQUEST_ID_HEADERS if lowered.get(name)), None)
    diagnostic = ProviderDiagnostic(
        response.status_code,
        provider_code,
        error_type,
        parameter,
        message,
        _redact_diagnostic(request_id, redact_values),
        raw,
        truncated,
    )
    error.provider_diagnostic = diagnostic  # type: ignore[attr-defined]


def _diagnostic_scalar(value: Any, secrets: tuple[str, ...]) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    return _redact_diagnostic(str(value)[:4000], secrets)


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    prompt_tokens_details: dict[str, Any] | None = None
    completion_tokens_details: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CompletionResult:
    content: str
    usage: ProviderUsage | None
    provider_request_id: str | None
    finish_reason: str | None


def build_chat_completion_request(
    profile: ModelProfileInput,
    *,
    prompt: str,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return {
        "model": profile.model_id,
        "messages": messages,
        **compose_inference_parameters(profile),
        "stream": False,
    }


def parse_completion_response(content: bytes, headers: Mapping[str, str]) -> CompletionResult:
    try:
        payload = json.loads(content)
        choices = payload["choices"]
        choice = choices[0]
        message_content = choice["message"]["content"]
        if not isinstance(message_content, str):
            raise TypeError
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError
        usage_value = payload.get("usage")
        usage = None if usage_value is None else ProviderUsage.model_validate(usage_value)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValidationError) as exc:
        raise InvalidProviderResponseError(
            "Providerresponse heeft geen geldig completioncontract."
        ) from exc
    lowered = {key.lower(): value for key, value in headers.items()}
    request_id = next((lowered[name] for name in _REQUEST_ID_HEADERS if lowered.get(name)), None)
    return CompletionResult(message_content, usage, request_id, finish_reason)


class ProviderHeaderResolver:
    def __init__(
        self,
        endpoint_repository: SQLiteEndpointRepository,
        secrets: SQLiteSecretRepository,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._endpoints = endpoint_repository
        self._secrets = secrets
        self._environment = os.environ if environment is None else environment

    def resolve(self, endpoint: EndpointRecord) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if endpoint.api_key_secret_id is not None:
            secret = self._secrets.get(endpoint.api_key_secret_id)
            if secret is None:
                raise ProviderClientError("Endpoint-API-key is niet beschikbaar.")
            headers[endpoint.data.api_key_header] = compose_api_key(
                endpoint.data.api_key_prefix, secret.value
            )
        secret_ids = self._endpoints.header_secret_ids(endpoint.id)
        for header in endpoint.data.extra_headers:
            if header.secret:
                secret_id = secret_ids.get(header.name.lower())
                secret = self._secrets.get(secret_id) if secret_id is not None else None
                if secret is None:
                    raise ProviderClientError("Geheime endpointheader is niet beschikbaar.")
                value = secret.value
            else:
                match = _ENV_REFERENCE.fullmatch(header.value)
                if match is not None:
                    value = self._environment.get(match.group(1), "")
                    if not value:
                        raise ProviderClientError("Environment-headersecret is niet beschikbaar.")
                else:
                    value = header.value
            headers[header.name] = value
        return headers


class OpenAIProviderClient:
    def __init__(
        self,
        header_resolver: ProviderHeaderResolver,
        *,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._headers = header_resolver
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def complete(
        self,
        endpoint: EndpointRecord,
        profile: ModelProfileInput,
        *,
        prompt: str,
        system_prompt: str | None = None,
    ) -> CompletionResult:
        if self._transport is None:
            validate_destination(endpoint, endpoint.data.completions_url)
        body = build_chat_completion_request(profile, prompt=prompt, system_prompt=system_prompt)
        encoded = simplejson.dumps(body, use_decimal=True, allow_nan=False).encode()
        timeout = httpx.Timeout(float(endpoint.data.timeout_seconds))
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    endpoint.data.completions_url,
                    headers=self._headers.resolve(endpoint),
                    content=encoded,
                ) as response:
                    self._classify_status(response)
                    content = self._read_limited(response)
                    return parse_completion_response(content, response.headers)
        except httpx.TimeoutException as exc:
            raise TimeoutError() from exc
        except httpx.NetworkError as exc:
            raise NetworkError() from exc
        except httpx.HTTPError as exc:
            raise NetworkError() from exc

    def _read_limited(self, response: httpx.Response) -> bytes:
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > self._max_response_bytes:
                    raise InvalidProviderResponseError(
                        "Providerresponse overschrijdt maximale grootte."
                    )
            except ValueError:
                pass
        result = bytearray()
        for chunk in response.iter_bytes():
            result.extend(chunk)
            if len(result) > self._max_response_bytes:
                raise InvalidProviderResponseError(
                    "Providerresponse overschrijdt maximale grootte."
                )
        return bytes(result)

    @staticmethod
    def _classify_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in {401, 403}:
            raise AuthenticationError()
        if status == 429:
            raise RateLimitError()
        if 400 <= status < 500:
            raise ProviderClientError()
        if status >= 500:
            raise ProviderServerError()
        if 300 <= status < 400:
            raise ProviderClientError("Providerredirects worden niet gevolgd.")
