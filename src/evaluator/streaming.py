"""Incremental SSE completion parsing and monotonic streaming metrics."""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from decimal import Decimal

import httpx
import simplejson
from pydantic import ValidationError

from evaluator.api import errors as api_errors
from evaluator.endpoints import EndpointRecord
from evaluator.models import ModelProfileInput
from evaluator.network_security import validate_destination
from evaluator.provider import (
    _REQUEST_ID_HEADERS,
    OpenAIProviderClient,
    ProviderHeaderResolver,
    ProviderUsage,
    attach_provider_diagnostic,
    build_chat_completion_request,
)
from evaluator.provider_outcomes import ProviderEvidence, is_guardrail_signal
from evaluator.time import Clock, SystemClock


class SseParser:
    """Parse arbitrarily fragmented UTF-8 SSE bytes into data payloads."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._data_lines: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        return self._process(self._decoder.decode(chunk), final=False)

    def finish(self) -> list[str]:
        return self._process(self._decoder.decode(b"", final=True), final=True)

    def _process(self, text: str, *, final: bool) -> list[str]:
        self._buffer += text
        events: list[str] = []
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                break
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1 :]
            if line.endswith("\r"):
                line = line[:-1]
            self._line(line, events)
        if final:
            if self._buffer:
                line, self._buffer = self._buffer.removesuffix("\r"), ""
                self._line(line, events)
            if self._data_lines:
                events.append("\n".join(self._data_lines))
                self._data_lines.clear()
        return events

    def _line(self, line: str, events: list[str]) -> None:
        if line == "":
            if self._data_lines:
                events.append("\n".join(self._data_lines))
                self._data_lines.clear()
            return
        if line.startswith(":"):
            return
        field, separator, value = line.partition(":")
        if field == "data":
            self._data_lines.append(value[1:] if separator and value.startswith(" ") else value)


@dataclass(frozen=True, slots=True)
class StreamingCompletionResult:
    content: str
    usage: ProviderUsage | None
    provider_request_id: str | None
    finish_reason: str | None
    total_latency_ms: Decimal
    ttft_ms: Decimal | None
    generation_duration_ms: Decimal | None
    tokens_per_second: Decimal | None
    successful: bool
    evaluate: bool
    error_code: str | None
    outcome_category: str
    provider_evidence: ProviderEvidence


class _Accumulator:
    def __init__(self, clock: Clock, started: float, max_bytes: int) -> None:
        self.clock = clock
        self.started = started
        self.max_bytes = max_bytes
        self.parts: list[str] = []
        self.answer_bytes = 0
        self.first_content_at: float | None = None
        self.last_content_at: float | None = None
        self.usage: ProviderUsage | None = None
        self.finish_reason: str | None = None
        self.done = False

    def event(self, data: str) -> None:
        if data == "[DONE]":
            self.done = True
            return
        payload = json.loads(data)
        usage_value = payload.get("usage")
        if usage_value is not None:
            self.usage = ProviderUsage.model_validate(usage_value)
        choices = payload.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("choices must be a list")
        if not choices:
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("choice must be an object")
        finish = choice.get("finish_reason")
        if finish is not None:
            if not isinstance(finish, str):
                raise ValueError("finish_reason must be a string")
            self.finish_reason = finish
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise ValueError("delta must be an object")
        content = delta.get("content")
        if content is None or content == "":
            return
        if not isinstance(content, str):
            raise ValueError("content delta must be a string")
        encoded_size = len(content.encode())
        if self.answer_bytes + encoded_size > self.max_bytes:
            raise OverflowError("answer size limit")
        now = self.clock.monotonic()
        if self.first_content_at is None:
            self.first_content_at = now
        self.last_content_at = now
        self.answer_bytes += encoded_size
        self.parts.append(content)

    def result(
        self, ended: float, request_id: str | None, error: str | None
    ) -> StreamingCompletionResult:
        ttft = (
            None
            if self.first_content_at is None
            else _elapsed_ms(self.first_content_at, self.started)
        )
        generation = None
        tps = None
        if (
            self.first_content_at is not None
            and self.last_content_at is not None
            and self.last_content_at > self.first_content_at
        ):
            seconds = Decimal(str(self.last_content_at)) - Decimal(str(self.first_content_at))
            generation = seconds * Decimal(1000)
            if self.usage is not None:
                tps = Decimal(self.usage.completion_tokens) / seconds
        valid_end = self.done or self.finish_reason is not None
        guarded = is_guardrail_signal(finish_reason=self.finish_reason, provider_code=None)
        successful = error is None and valid_end and not guarded
        evidence = ProviderEvidence(
            finish_reason=self.finish_reason,
            provider_request_id=request_id,
        )
        return StreamingCompletionResult(
            content="".join(self.parts),
            usage=self.usage,
            provider_request_id=request_id,
            finish_reason=self.finish_reason,
            total_latency_ms=_elapsed_ms(ended, self.started),
            ttft_ms=ttft,
            generation_duration_ms=generation,
            tokens_per_second=tps,
            successful=successful,
            evaluate=successful,
            error_code=(
                error
                if error is not None
                else "provider_guardrail"
                if guarded
                else None
                if valid_end
                else "unexpected_eof"
            ),
            outcome_category=(
                "provider_guardrail" if guarded else "normal" if successful else "provider_error"
            ),
            provider_evidence=evidence,
        )


def _elapsed_ms(end: float, start: float) -> Decimal:
    return (Decimal(str(end)) - Decimal(str(start))) * Decimal(1000)


class OpenAIStreamingClient:
    def __init__(
        self,
        header_resolver: ProviderHeaderResolver,
        *,
        max_response_bytes: int,
        max_answer_bytes: int | None = None,
        clock: Clock | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_response_bytes <= 0 or (max_answer_bytes is not None and max_answer_bytes <= 0):
            raise ValueError("response and answer limits must be positive")
        self._headers = header_resolver
        self._max_bytes = max_response_bytes
        self._max_answer_bytes = max_answer_bytes or max_response_bytes
        self._clock = clock or SystemClock()
        self._transport = transport
        self._client = httpx.Client(
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        """Close transport, interrupting active streams during application shutdown."""

        self._client.close()

    def complete(
        self,
        endpoint: EndpointRecord,
        profile: ModelProfileInput,
        *,
        prompt: str,
        system_prompt: str | None = None,
        diagnostic_mode: bool = False,
    ) -> StreamingCompletionResult:
        if self._transport is None:
            validate_destination(endpoint, endpoint.data.completions_url)
        body = build_chat_completion_request(profile, prompt=prompt, system_prompt=system_prompt)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        encoded = simplejson.dumps(body, use_decimal=True, allow_nan=False).encode()
        started = self._clock.monotonic()
        accumulator = _Accumulator(self._clock, started, self._max_answer_bytes)
        parser = SseParser()
        request_id: str | None = None
        wire_bytes = 0
        try:
            resolved_headers = self._headers.resolve(endpoint)
            with self._client.stream(
                "POST",
                endpoint.data.completions_url,
                headers=resolved_headers,
                content=encoded,
                timeout=httpx.Timeout(float(endpoint.data.timeout_seconds)),
            ) as response:
                try:
                    OpenAIProviderClient._classify_status(response)
                except api_errors.ApiError as exc:
                    if diagnostic_mode:
                        attach_provider_diagnostic(
                            exc,
                            response,
                            redact_values=tuple(resolved_headers.values()),
                        )
                    raise
                lowered = {key.lower(): value for key, value in response.headers.items()}
                request_id = next(
                    (lowered[name] for name in _REQUEST_ID_HEADERS if lowered.get(name)), None
                )
                for chunk in response.iter_raw():
                    wire_bytes += len(chunk)
                    if wire_bytes > self._max_bytes:
                        return accumulator.result(
                            self._clock.monotonic(), request_id, "stream_too_large"
                        )
                    for event in parser.feed(chunk):
                        accumulator.event(event)
                for event in parser.finish():
                    accumulator.event(event)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
            return accumulator.result(self._clock.monotonic(), request_id, "invalid_stream")
        except OverflowError:
            return accumulator.result(self._clock.monotonic(), request_id, "answer_too_large")
        except httpx.TimeoutException as exc:
            if wire_bytes == 0:
                raise api_errors.TimeoutError(
                    "Provider request timed out before response data."
                ) from exc
            return accumulator.result(self._clock.monotonic(), request_id, "timeout")
        except httpx.NetworkError as exc:
            if wire_bytes == 0:
                raise api_errors.NetworkError(
                    "Provider network request failed before response data."
                ) from exc
            return accumulator.result(self._clock.monotonic(), request_id, "network_error")
        except httpx.HTTPError as exc:
            if wire_bytes == 0:
                raise api_errors.NetworkError(
                    "Provider HTTP transport failed before response data."
                ) from exc
            return accumulator.result(self._clock.monotonic(), request_id, "network_error")
        return accumulator.result(self._clock.monotonic(), request_id, None)
