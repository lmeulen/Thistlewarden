"""Safe OpenAI-compatible non-streaming request construction and HTTP client."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

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
from evaluator.provider_outcomes import ProviderEvidence, error_evidence, is_guardrail_signal
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


class ConversationMessage(BaseModel):
    """Provider-neutral conversational context for target completion requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


def _conversation_messages(
    *,
    prompt: str | None,
    messages: Sequence[ConversationMessage] | None,
) -> list[dict[str, str]]:
    if prompt is not None and messages is not None:
        raise ValueError("prompt and messages are mutually exclusive")
    if messages is None:
        if prompt is None:
            raise ValueError("prompt or messages is required")
        messages = (ConversationMessage(role="user", content=prompt),)
    if not messages:
        raise ValueError("messages must not be empty")
    result: list[dict[str, str]] = []
    expected_role: Literal["user", "assistant"] = "user"
    for message in messages:
        validated = ConversationMessage.model_validate(message)
        if not validated.content.strip():
            raise ValueError("message content must not be blank")
        if validated.role != expected_role:
            raise ValueError("messages must alternate user and assistant roles, starting with user")
        result.append(validated.model_dump())
        expected_role = "assistant" if expected_role == "user" else "user"
    if result[-1]["role"] != "user":
        raise ValueError("messages must end with a user message")
    return result


@dataclass(frozen=True, slots=True)
class CompletionResult:
    content: str
    usage: ProviderUsage | None
    provider_request_id: str | None
    finish_reason: str | None
    successful: bool = True
    evaluate: bool = True
    error_code: str | None = None
    outcome_category: str = "normal"
    provider_evidence: ProviderEvidence = ProviderEvidence()


class ProviderGuardrailError(ProviderClientError):
    """Provider deliberately blocked generation under a policy or guardrail."""

    code = "PROVIDER_GUARDRAIL"
    default_message = "Provider blocked generation under its content policy."

    def __init__(self, evidence: ProviderEvidence) -> None:
        super().__init__()
        self.evidence = evidence


def build_chat_completion_request(
    profile: ModelProfileInput,
    *,
    prompt: str | None = None,
    messages: Sequence[ConversationMessage] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    provider_messages = _conversation_messages(prompt=prompt, messages=messages)
    if system_prompt is not None:
        if not system_prompt.strip():
            raise ValueError("system prompt must not be blank")
        provider_messages.insert(0, {"role": "system", "content": system_prompt})
    return {
        "model": profile.model_id,
        "messages": provider_messages,
        **compose_inference_parameters(profile),
        "stream": False,
    }


def build_anthropic_completion_request(
    profile: ModelProfileInput,
    *,
    prompt: str | None = None,
    messages: Sequence[ConversationMessage] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    if profile.max_tokens is None:
        raise ProviderClientError(
            "Anthropic-compatible models require a configured maximum output token count."
        )
    if profile.max_tokens_field_path != "max_tokens":
        raise ProviderClientError(
            "Anthropic-compatible models support only the max_tokens output-token field."
        )
    parameters = compose_inference_parameters(profile)
    max_tokens = parameters.pop("max_tokens", None)
    if max_tokens is None or not isinstance(max_tokens, int):
        raise ProviderClientError(
            "Anthropic-compatible models require max_tokens as a top-level integer."
        )
    body: dict[str, Any] = {
        "model": profile.model_id,
        "messages": _conversation_messages(prompt=prompt, messages=messages),
        "max_tokens": max_tokens,
        **parameters,
        "stream": False,
    }
    if system_prompt is not None:
        if not system_prompt.strip():
            raise ValueError("system prompt must not be blank")
        body["system"] = system_prompt
    return body


def parse_completion_response(content: bytes, headers: Mapping[str, str]) -> CompletionResult:
    try:
        payload = json.loads(content)
        choices = payload["choices"]
        choice = choices[0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError
        message_content = message.get("content")
        refusal_present = message.get("refusal") is not None
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError
        guarded = refusal_present or is_guardrail_signal(
            finish_reason=finish_reason, provider_code=None
        )
        if not guarded and not isinstance(message_content, str):
            raise TypeError
        if guarded and message_content is not None and not isinstance(message_content, str):
            raise TypeError
        usage_value = payload.get("usage")
        usage = None if usage_value is None else ProviderUsage.model_validate(usage_value)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValidationError) as exc:
        raise InvalidProviderResponseError(
            "Provider response does not match the completion contract."
        ) from exc
    lowered = {key.lower(): value for key, value in headers.items()}
    request_id = next((lowered[name] for name in _REQUEST_ID_HEADERS if lowered.get(name)), None)
    evidence = ProviderEvidence(
        finish_reason=finish_reason,
        provider_request_id=request_id,
        refusal_present=refusal_present,
    )
    return CompletionResult(
        message_content or "",
        usage,
        request_id,
        finish_reason,
        successful=not guarded,
        evaluate=not guarded,
        error_code="provider_guardrail" if guarded else None,
        outcome_category="provider_guardrail" if guarded else "normal",
        provider_evidence=evidence,
    )


def parse_anthropic_completion_response(
    content: bytes, headers: Mapping[str, str]
) -> CompletionResult:
    try:
        payload = json.loads(content)
        blocks = payload["content"]
        if not isinstance(blocks, list):
            raise TypeError
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise TypeError
            if block.get("type") != "text":
                raise TypeError
            text = block.get("text")
            if not isinstance(text, str):
                raise TypeError
            parts.append(text)
        finish_reason = payload.get("stop_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError
        usage_value = payload.get("usage")
        usage = None
        if usage_value is not None:
            if not isinstance(usage_value, dict):
                raise TypeError
            input_tokens = usage_value.get("input_tokens")
            output_tokens = usage_value.get("output_tokens")
            if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
                raise TypeError
            usage = ProviderUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        raise InvalidProviderResponseError(
            "Provider response does not match the Anthropic Messages contract."
        ) from exc
    lowered = {key.lower(): value for key, value in headers.items()}
    request_id = next((lowered[name] for name in _REQUEST_ID_HEADERS if lowered.get(name)), None)
    guarded = is_guardrail_signal(finish_reason=finish_reason, provider_code=None)
    evidence = ProviderEvidence(finish_reason=finish_reason, provider_request_id=request_id)
    return CompletionResult(
        "".join(parts),
        usage,
        request_id,
        finish_reason,
        successful=not guarded,
        evaluate=not guarded,
        error_code="provider_guardrail" if guarded else None,
        outcome_category="provider_guardrail" if guarded else "normal",
        provider_evidence=evidence,
    )


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
                raise ProviderClientError("Endpoint API key is unavailable.")
            headers[endpoint.data.api_key_header] = compose_api_key(
                endpoint.data.api_key_prefix, secret.value
            )
        secret_ids = self._endpoints.header_secret_ids(endpoint.id)
        for header in endpoint.data.extra_headers:
            if header.secret:
                secret_id = secret_ids.get(header.name.lower())
                secret = self._secrets.get(secret_id) if secret_id is not None else None
                if secret is None:
                    raise ProviderClientError("Secret endpoint header is unavailable.")
                value = secret.value
            else:
                match = _ENV_REFERENCE.fullmatch(header.value)
                if match is not None:
                    value = self._environment.get(match.group(1), "")
                    if not value:
                        raise ProviderClientError(
                            "Environment-backed header secret is unavailable."
                        )
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
        self._client = httpx.Client(
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        """Close transport, interrupting active requests during application shutdown."""

        self._client.close()

    def complete(
        self,
        endpoint: EndpointRecord,
        profile: ModelProfileInput,
        *,
        prompt: str | None = None,
        messages: Sequence[ConversationMessage] | None = None,
        system_prompt: str | None = None,
    ) -> CompletionResult:
        if self._transport is None:
            validate_destination(endpoint, endpoint.data.completions_url)
        body = (
            build_anthropic_completion_request(
                profile, prompt=prompt, messages=messages, system_prompt=system_prompt
            )
            if endpoint.data.api_format == "anthropic"
            else build_chat_completion_request(
                profile, prompt=prompt, messages=messages, system_prompt=system_prompt
            )
        )
        encoded = simplejson.dumps(body, use_decimal=True, allow_nan=False).encode()
        timeout = httpx.Timeout(float(endpoint.data.timeout_seconds))
        try:
            with self._client.stream(
                "POST",
                endpoint.data.completions_url,
                headers=self._headers.resolve(endpoint),
                content=encoded,
                timeout=timeout,
            ) as response:
                self._classify_status(response)
                content = self._read_limited(response)
                if endpoint.data.api_format == "anthropic":
                    return parse_anthropic_completion_response(content, response.headers)
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
                    raise InvalidProviderResponseError("Provider response exceeds maximum size.")
            except ValueError:
                pass
        result = bytearray()
        for chunk in response.iter_bytes():
            result.extend(chunk)
            if len(result) > self._max_response_bytes:
                raise InvalidProviderResponseError("Provider response exceeds maximum size.")
        return bytes(result)

    @staticmethod
    def _classify_status(response: httpx.Response) -> None:
        status = response.status_code
        if 400 <= status < 500:
            length = response.headers.get("content-length")
            content = b""
            if length is not None:
                try:
                    if int(length) <= _DIAGNOSTIC_BODY_LIMIT:
                        content = response.read()
                except ValueError:
                    pass
            evidence = error_evidence(content, dict(response.headers))
            if is_guardrail_signal(
                finish_reason=evidence.finish_reason, provider_code=evidence.provider_code
            ):
                raise ProviderGuardrailError(evidence)
        if status in {401, 403}:
            raise AuthenticationError()
        if status == 429:
            raise RateLimitError()
        if 400 <= status < 500:
            raise ProviderClientError()
        if status >= 500:
            raise ProviderServerError()
        if 300 <= status < 400:
            raise ProviderClientError("Provider redirects are not followed.")
