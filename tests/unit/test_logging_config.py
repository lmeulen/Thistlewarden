from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest

from evaluator.logging_config import (
    LOG_FORMAT,
    MAX_LOG_LINE_BYTES,
    SafeContextFilter,
    SafeLogFormatter,
    SecretRedactor,
    configure_logging,
    log_context,
    log_event,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    yield
    root.handlers[:] = old_handlers
    root.setLevel(old_level)


def test_log_format_contains_only_safe_context_fields() -> None:
    assert "request_id" in LOG_FORMAT
    assert "run_id" in LOG_FORMAT
    assert "case_id" in LOG_FORMAT
    assert "execution_id" in LOG_FORMAT
    for forbidden in ("body", "prompt", "answer", "expected_answer"):
        assert forbidden not in LOG_FORMAT


def test_context_filter_supplies_missing_ids() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe", (), None)

    assert SafeContextFilter().filter(record) is True
    assert record.request_id == "-"
    assert record.run_id == "-"
    assert record.case_id == "-"
    assert record.execution_id == "-"


def test_redactor_removes_full_and_prefixed_secret_values() -> None:
    redactor = SecretRedactor(("fixture-secret", "Bearer fixture-secret"))

    assert redactor.redact("Authorization: Bearer fixture-secret") == "Authorization: [REDACTED]"


def test_filter_redacts_formatted_arguments() -> None:
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed token=%s", ("fixture-secret",), None
    )

    SafeContextFilter(("fixture-secret",)).filter(record)

    assert record.getMessage() == "failed token=[REDACTED]"


def test_log_context_binds_and_restores_allowlisted_ids() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe", (), None)
    with log_context(run_id="run-1", execution_id="exec-1"):
        SafeContextFilter().filter(record)

    assert record.run_id == "run-1"
    assert record.execution_id == "exec-1"
    with pytest.raises(ValueError, match="Unsupported"):
        with log_context(prompt="forbidden"):
            pass


def test_log_event_emits_allowlisted_sorted_fields(caplog) -> None:
    logger = logging.getLogger("test.events")
    with caplog.at_level(logging.INFO, logger="test.events"):
        log_event(logger, logging.INFO, "run_created", status="pending", count=3)

    assert caplog.records[-1].getMessage() == "run_created count=3 status=pending"


def test_log_event_converts_safe_types_escapes_lines_and_limits_values(caplog) -> None:
    class Status(Enum):
        READY = "ready"

    logger = logging.getLogger("test.events")
    with caplog.at_level(logging.INFO, logger="test.events"):
        log_event(
            logger,
            logging.INFO,
            "safe_types",
            object_id=UUID("10000000-0000-4000-8000-000000000001"),
            score=Decimal("0.75"),
            path=Path("safe\npath"),
            status=Status.READY,
            reason="x" * 600,
        )

    message = caplog.records[-1].getMessage()
    assert "object_id=10000000-0000-4000-8000-000000000001" in message
    assert "score=0.75" in message
    assert "path=safe\\npath" in message
    assert "status=Status.READY" in message
    assert "reason=" + ("x" * 500) in message
    assert "x" * 501 not in message


def test_log_event_skips_field_conversion_when_level_is_disabled() -> None:
    logger = logging.getLogger("test.disabled")
    logger.setLevel(logging.ERROR)

    log_event(logger, logging.DEBUG, "disabled_event", status=object())


def test_safe_formatter_limits_complete_utf8_line_and_hides_exception_text() -> None:
    formatter = SafeLogFormatter(LOG_FORMAT)
    record = logging.LogRecord("x" * 9000, logging.ERROR, __file__, 1, "safe", (), None)
    SafeContextFilter().filter(record)

    assert len(formatter.format(record).encode("utf-8")) <= MAX_LOG_LINE_BYTES

    try:
        raise RuntimeError("forbidden-exception-detail")
    except RuntimeError:
        exception_record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", (), exc_info=__import__("sys").exc_info()
        )
    SafeContextFilter().filter(exception_record)
    rendered = formatter.format(exception_record)
    assert "exception_type=RuntimeError" in rendered
    assert "forbidden-exception-detail" not in rendered


def test_log_context_isolated_between_threads_and_reset_after_exception() -> None:
    def render(run_id: str) -> str:
        with log_context(run_id=run_id):
            record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe", (), None)
            SafeContextFilter().filter(record)
            return str(record.run_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert set(executor.map(render, ("run-a", "run-b"))) == {"run-a", "run-b"}

    with pytest.raises(RuntimeError, match="stop"):
        with log_context(run_id="temporary"):
            raise RuntimeError("stop")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "safe", (), None)
    SafeContextFilter().filter(record)
    assert record.run_id == "-"


def test_log_event_rejects_sensitive_or_arbitrary_fields() -> None:
    logger = logging.getLogger("test.events")
    with pytest.raises(ValueError, match="Unsafe"):
        log_event(logger, logging.INFO, "bad_event", prompt="never log")
    with pytest.raises(ValueError, match="Unsafe"):
        log_event(logger, logging.INFO, "bad_event", arbitrary="value")
    with pytest.raises(ValueError, match="snake_case"):
        log_event(logger, logging.INFO, "Bad Event")


def test_configure_logging_uses_requested_level() -> None:
    configure_logging("DEBUG")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, logging.Formatter)
