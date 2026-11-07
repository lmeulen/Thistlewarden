"""Time helpers for persistent UTC timestamps."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now_utc(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


def utc_now() -> datetime:
    """Return a timezone-aware current UTC timestamp."""

    return datetime.now(UTC)


def from_utc_iso(value: str) -> datetime:
    """Parse persisted ISO 8601 timestamp into timezone-aware UTC."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted datetime must be timezone-aware")
    return parsed.astimezone(UTC)


def to_utc_iso(value: datetime) -> str:
    """Serialize a timezone-aware datetime as normalized ISO 8601 UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
