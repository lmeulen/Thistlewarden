from __future__ import annotations

import random
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from evaluator.api.errors import (
    CancelledError,
    NetworkError,
    ProviderClientError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
)
from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.rate_limits import (
    CentralEndpointLimiter,
    ProviderCallExecutor,
    RetryPolicy,
    is_retryable,
    parse_retry_after,
)

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self, monotonic: float = 0, now: datetime | None = None) -> None:
        self.value = monotonic
        self.utc = now or datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def now_utc(self) -> datetime:
        return self.utc


def _endpoint(**changes) -> EndpointRecord:
    now = datetime.now(UTC)
    data = EndpointInput(name="p", completions_url="https://provider.test", **changes)
    return EndpointRecord(uuid4(), data, None, now, now)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), True),
        (NetworkError(), True),
        (RateLimitError(), True),
        (ProviderServerError(), True),
        (ProviderClientError(), False),
        (ValueError(), False),
    ],
)
def test_retry_classification(error: BaseException, expected: bool) -> None:
    assert is_retryable(error) is expected


def test_exponential_backoff_seeded_jitter_and_retry_after_dual_clock() -> None:
    clock = FakeClock(now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    policy = RetryPolicy(
        clock=clock,
        random_source=random.Random(7),
        base_seconds=2,
        max_seconds=5,
        jitter_ratio=0.25,
    )
    expected_random = random.Random(7)
    assert policy.delay(1) == 2 * (1 + expected_random.uniform(-0.25, 0.25))
    assert policy.delay(2) == 4 * (1 + expected_random.uniform(-0.25, 0.25))
    assert policy.delay(3) == 5 * (1 + expected_random.uniform(-0.25, 0.25))
    assert parse_retry_after("3.5", clock) == 3.5
    date = (clock.utc + timedelta(seconds=9)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(date, clock) == 9
    clock.value = 9999
    assert parse_retry_after(date, clock) == 9
    assert parse_retry_after("invalid", clock) is None


def test_rpm_window_uses_monotonic_time() -> None:
    clock = FakeClock()
    limiter = CentralEndpointLimiter(clock=clock, wait_interval_seconds=0.005)
    endpoint = _endpoint(requests_per_minute=1)
    limiter.acquire(endpoint).release()
    acquired = threading.Event()

    def worker() -> None:
        limiter.acquire(endpoint).release()
        acquired.set()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.02)
    assert not acquired.is_set()
    clock.value = 60.0
    state = limiter._state(endpoint.id)  # noqa: SLF001
    with state.condition:
        state.condition.notify_all()
    thread.join(1)
    assert acquired.is_set()


def test_concurrency_permit_released_and_wait_event_observed() -> None:
    events = []
    limiter = CentralEndpointLimiter(observer=events.append, wait_interval_seconds=0.005)
    endpoint = _endpoint(max_concurrency=1)
    first = limiter.acquire(endpoint)
    acquired = threading.Event()

    def worker() -> None:
        with limiter.acquire(endpoint):
            acquired.set()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.02)
    assert not acquired.is_set()
    first.release()
    thread.join(1)
    assert acquired.is_set()
    assert any(
        event.kind == "rate_limited_wait" and event.reason == "concurrency" for event in events
    )


def test_tpm_reservation_reconciles_actual_and_unknown_keeps_estimate() -> None:
    limiter = CentralEndpointLimiter(wait_interval_seconds=0.005)
    endpoint = _endpoint(tokens_per_minute=10, max_concurrency=2)
    first = limiter.acquire(endpoint, estimated_tokens=8)
    first.reconcile(3)
    with limiter.acquire(endpoint, estimated_tokens=7) as second:
        second.reconcile(None)
        assert second.usage_unknown is True
    first.release()
    with pytest.raises(RateLimitError):
        limiter.acquire(endpoint, estimated_tokens=11)


def test_shutdown_and_cancellation_wake_waiters_without_permit_leak() -> None:
    limiter = CentralEndpointLimiter(wait_interval_seconds=10)
    endpoint = _endpoint(max_concurrency=1)
    first = limiter.acquire(endpoint)
    errors = []
    thread = threading.Thread(target=lambda: _capture_cancel(limiter, endpoint, errors))
    thread.start()
    time.sleep(0.02)
    limiter.close()
    thread.join(1)
    first.release()
    assert errors and isinstance(errors[0], CancelledError)


def _capture_cancel(limiter, endpoint, errors) -> None:
    try:
        limiter.acquire(endpoint)
    except BaseException as exc:
        errors.append(exc)


def test_executor_retries_and_releases_concurrency_on_errors() -> None:
    endpoint = _endpoint(max_retries=1, max_concurrency=1)
    events = []
    sleeps = []
    policy = RetryPolicy(sleeper=sleeps.append, jitter_ratio=0)
    policy.sleep = lambda seconds, cancelled=None: sleeps.append(seconds)  # type: ignore[method-assign]
    executor = ProviderCallExecutor(CentralEndpointLimiter(), policy, events.append)
    calls = 0

    def call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderServerError()
        return "ok"

    assert (
        executor.execute(endpoint, call, estimated_tokens=2, usage_tokens=lambda result: 1) == "ok"
    )
    assert calls == 2 and sleeps == [1.0]
    assert events[0].kind == "retry_wait"
