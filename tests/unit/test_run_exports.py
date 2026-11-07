from datetime import UTC, datetime

import pytest

from evaluator.run_exports import safe_filename

pytestmark = pytest.mark.unit


def test_safe_filename() -> None:
    assert safe_filename("../../hé <run>") == "h-run"
    assert safe_filename("...") == "run"


def test_clock_fixture_is_timezone_aware() -> None:
    assert datetime(2026, 1, 1, tzinfo=UTC).utcoffset() is not None
