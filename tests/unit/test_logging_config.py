from __future__ import annotations

import logging

import pytest

from evaluator.logging_config import (
    LOG_FORMAT,
    SafeContextFilter,
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
