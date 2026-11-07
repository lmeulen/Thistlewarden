"""Safe structured application logging with correlation context."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Final
from uuid import UUID

LOG_FORMAT: Final = (
    "%(asctime)s level=%(levelname)s component=%(name)s "
    "request_id=%(request_id)s run_id=%(run_id)s case_id=%(case_id)s "
    "execution_id=%(execution_id)s message=%(message)s"
)
_CONTEXT_FIELDS: Final = ("request_id", "run_id", "case_id", "execution_id")
_CONTEXT: dict[str, ContextVar[str]] = {
    field: ContextVar(f"log_{field}", default="-") for field in _CONTEXT_FIELDS
}
_OPERATION_ACTOR: ContextVar[str] = ContextVar("log_operation_actor", default="internal")
_WIRE_LOGGERS: Final = ("urllib3", "httpx", "httpcore")
_FORBIDDEN_FIELD_PARTS: Final = (
    "secret",
    "token",
    "authorization",
    "cookie",
    "csrf",
    "prompt",
    "answer",
    "body",
    "content",
    "header",
    "key",
)
_ALLOWED_FIELDS: Final = frozenset(
    {
        "action",
        "actor",
        "setting",
        "attempt",
        "bytes",
        "case_count",
        "category",
        "component",
        "concurrency",
        "configured_gates",
        "conflicts",
        "count",
        "database",
        "delay_ms",
        "deleted_runs",
        "done",
        "duration_ms",
        "enabled",
        "endpoint_id",
        "error_code",
        "errors",
        "evaluator_method",
        "event_count",
        "exception_type",
        "finish_reason",
        "format",
        "gate_status",
        "generation",
        "host",
        "interface",
        "job_id",
        "max_retries",
        "method",
        "missing_count",
        "mode",
        "model_count",
        "model_id",
        "object_count",
        "object_id",
        "object_type",
        "outcome",
        "path",
        "port",
        "preview",
        "profile_id",
        "reason",
        "recovered_jobs",
        "recovered_runs",
        "request_bytes",
        "response_bytes",
        "route",
        "score",
        "source",
        "status",
        "status_code",
        "stopped",
        "target_models",
        "threshold",
        "total",
        "ttft_ms",
        "usage_present",
        "wait_ms",
        "workers_enabled",
    }
)
MAX_LOG_LINE_BYTES: Final = 8192
_MAX_VALUE_LENGTH: Final = 500
_MAX_MESSAGE_LENGTH: Final = 6000


class SafeLogFormatter(logging.Formatter):
    """Format bounded UTF-8 lines without rendering exception messages."""

    def formatException(
        self,
        exc_info: (
            tuple[type[BaseException], BaseException, TracebackType | None]
            | tuple[None, None, None]
        ),
    ) -> str:
        exception_type = exc_info[0]
        if exception_type is None:
            return "exception_type=unknown"
        return f"exception_type={exception_type.__name__}"

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        encoded = rendered.encode("utf-8")
        if len(encoded) <= MAX_LOG_LINE_BYTES:
            return rendered
        return encoded[:MAX_LOG_LINE_BYTES].decode("utf-8", errors="ignore")


class SecretRedactor:
    """Exact-value redaction as defense in depth; content logging remains forbidden."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )

    def redact(self, value: object) -> str:
        redacted = str(value)
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted


class SafeContextFilter(logging.Filter):
    """Supply allowlisted correlation fields and redact registered secrets."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._redactor = SecretRedactor(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        for field in _CONTEXT_FIELDS:
            existing = getattr(record, field, None)
            value = existing if existing is not None else _CONTEXT[field].get()
            setattr(record, field, self._redactor.redact(value))
        record.msg = self._redactor.redact(record.getMessage())[:_MAX_MESSAGE_LENGTH]
        record.args = ()
        return True


def _safe_value(value: object) -> str:
    if isinstance(value, (UUID, Decimal, Path, Enum)):
        value = str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        rendered = str(value)
    else:
        raise TypeError(f"Unsupported log field value: {type(value).__name__}")
    return rendered.replace("\r", "\\r").replace("\n", "\\n")[:_MAX_VALUE_LENGTH]


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    /,
    **fields: object,
) -> None:
    """Emit stable event plus allowlisted primitive metadata, never arbitrary content."""

    if not event or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in event
    ):
        raise ValueError("log event must be non-empty lowercase snake_case")
    unknown = set(fields) - _ALLOWED_FIELDS
    forbidden = {
        name for name in fields if any(part in name.lower() for part in _FORBIDDEN_FIELD_PARTS)
    }
    if unknown or forbidden:
        raise ValueError(f"Unsafe or unsupported log fields: {sorted(unknown | forbidden)}")
    if not logger.isEnabledFor(level):
        return
    suffix = " ".join(f"{name}={_safe_value(fields[name])}" for name in sorted(fields))
    logger.log(level, event if not suffix else f"{event} {suffix}")


def log_semantic_event(
    logger: logging.Logger,
    event: str,
    /,
    **fields: object,
) -> None:
    """Emit post-commit INFO event with actor from current operation context."""

    log_event(logger, logging.INFO, event, actor=_OPERATION_ACTOR.get(), **fields)


def bind_operation_actor(actor: str) -> Token[str]:
    """Bind trusted operation actor until reset with returned token."""

    if actor not in {"api", "local_gui", "internal"}:
        raise ValueError(f"Unsupported operation actor: {actor}")
    return _OPERATION_ACTOR.set(actor)


def reset_operation_actor(token: Token[str]) -> None:
    _OPERATION_ACTOR.reset(token)


def bind_log_context(**correlation_ids: str) -> list[tuple[ContextVar[str], Token[str]]]:
    """Bind correlation IDs until tokens are reset by ``reset_log_context``."""

    unknown = set(correlation_ids) - set(_CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported log context fields: {sorted(unknown)}")
    return [
        (_CONTEXT[field], _CONTEXT[field].set(str(value)))
        for field, value in correlation_ids.items()
    ]


def reset_log_context(tokens: list[tuple[ContextVar[str], Token[str]]]) -> None:
    for variable, token in reversed(tokens):
        variable.reset(token)


@contextmanager
def log_context(**correlation_ids: str) -> Iterator[None]:
    """Bind only known correlation IDs for current thread/task context."""

    tokens = bind_log_context(**correlation_ids)
    try:
        yield
    finally:
        reset_log_context(tokens)


def configure_logging(level: str, *, secrets: Iterable[str] = ()) -> None:
    """Configure content-free structured logging and disable HTTP wire debug logs."""

    handler = logging.StreamHandler()
    handler.setFormatter(SafeLogFormatter(LOG_FORMAT))
    handler.addFilter(SafeContextFilter(secrets))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for logger_name in _WIRE_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
