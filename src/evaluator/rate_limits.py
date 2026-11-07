"""Thread-safe endpoint limits and deterministic provider retry policy."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TypeVar
from uuid import UUID, uuid4

from evaluator.api.errors import (
    CancelledError,
    NetworkError,
    ProviderServerError,
    RateLimitError,
    TimeoutError,
)
from evaluator.endpoints import EndpointRecord
from evaluator.logging_config import log_event
from evaluator.time import Clock, SystemClock

T = TypeVar("T")
_LOG = logging.getLogger(__name__)
Observer = Callable[["LimiterEvent"], None]


@dataclass(frozen=True, slots=True)
class LimiterEvent:
    kind: str
    endpoint_id: UUID
    reason: str | None = None
    attempt: int | None = None
    delay_seconds: float | None = None


@dataclass(slots=True)
class _TokenCharge:
    id: UUID
    timestamp: float
    tokens: int


@dataclass(slots=True)
class _EndpointState:
    condition: threading.Condition = field(default_factory=threading.Condition)
    requests: deque[float] = field(default_factory=deque)
    tokens: deque[_TokenCharge] = field(default_factory=deque)
    active: int = 0
    next_ticket: int = 0
    serving_ticket: int = 0
    abandoned_tickets: set[int] = field(default_factory=set)


class EndpointPermit:
    def __init__(
        self,
        limiter: CentralEndpointLimiter,
        endpoint_id: UUID,
        charge_id: UUID | None,
        estimated_tokens: int,
    ) -> None:
        self._limiter = limiter
        self.endpoint_id = endpoint_id
        self._charge_id = charge_id
        self.estimated_tokens = estimated_tokens
        self._released = False
        self.usage_unknown = False

    def reconcile(self, actual_tokens: int | None) -> None:
        if actual_tokens is not None and actual_tokens < 0:
            raise ValueError("actual tokens must be non-negative")
        self.usage_unknown = actual_tokens is None
        self._limiter._reconcile(self.endpoint_id, self._charge_id, actual_tokens)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter._release(self.endpoint_id)

    def __enter__(self) -> EndpointPermit:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class CentralEndpointLimiter:
    WINDOW_SECONDS = 60.0

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        observer: Observer | None = None,
        wait_interval_seconds: float = 0.05,
    ) -> None:
        self._clock = clock or SystemClock()
        self._observer = observer
        self._wait_interval = wait_interval_seconds
        self._states: dict[UUID, _EndpointState] = {}
        self._states_lock = threading.Lock()
        self._closed = False

    def _state(self, endpoint_id: UUID) -> _EndpointState:
        with self._states_lock:
            return self._states.setdefault(endpoint_id, _EndpointState())

    def acquire(
        self,
        endpoint: EndpointRecord,
        *,
        estimated_tokens: int = 0,
        cancelled: threading.Event | None = None,
    ) -> EndpointPermit:
        if estimated_tokens < 0:
            raise ValueError("estimated tokens must be non-negative")
        data = endpoint.data
        if data.tokens_per_minute is not None and estimated_tokens > data.tokens_per_minute:
            raise RateLimitError("Estimated token demand exceeds endpoint TPM.")
        state = self._state(endpoint.id)
        with state.condition:
            ticket = state.next_ticket
            state.next_ticket += 1
            waiting_reported = False
            while True:
                if self._closed or (cancelled is not None and cancelled.is_set()):
                    state.abandoned_tickets.add(ticket)
                    self._advance_tickets(state)
                    state.condition.notify_all()
                    raise CancelledError("Limiter is gesloten of call is geannuleerd.")
                now = self._clock.monotonic()
                self._expire(state, now)
                reason = self._blocked_reason(
                    state,
                    data.requests_per_minute,
                    data.tokens_per_minute,
                    data.max_concurrency,
                    estimated_tokens,
                    ticket,
                )
                if reason is None:
                    state.requests.append(now)
                    charge_id = None
                    if data.tokens_per_minute is not None:
                        charge_id = uuid4()
                        state.tokens.append(_TokenCharge(charge_id, now, estimated_tokens))
                    state.active += 1
                    state.serving_ticket += 1
                    self._advance_tickets(state)
                    state.condition.notify_all()
                    self._emit(LimiterEvent("acquired", endpoint.id))
                    return EndpointPermit(self, endpoint.id, charge_id, estimated_tokens)
                if not waiting_reported:
                    self._emit(LimiterEvent("rate_limited_wait", endpoint.id, reason=reason))
                    waiting_reported = True
                state.condition.wait(self._wait_interval)

    @staticmethod
    def _advance_tickets(state: _EndpointState) -> None:
        while state.serving_ticket in state.abandoned_tickets:
            state.abandoned_tickets.remove(state.serving_ticket)
            state.serving_ticket += 1

    def _blocked_reason(
        self,
        state: _EndpointState,
        rpm: int | None,
        tpm: int | None,
        concurrency: int | None,
        estimated: int,
        ticket: int,
    ) -> str | None:
        if ticket != state.serving_ticket:
            return "fifo"
        if concurrency is not None and state.active >= concurrency:
            return "concurrency"
        if rpm is not None and len(state.requests) >= rpm:
            return "rpm"
        if tpm is not None and sum(item.tokens for item in state.tokens) + estimated > tpm:
            return "tpm"
        return None

    def _expire(self, state: _EndpointState, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while state.requests and state.requests[0] <= cutoff:
            state.requests.popleft()
        while state.tokens and state.tokens[0].timestamp <= cutoff:
            state.tokens.popleft()

    def _reconcile(
        self, endpoint_id: UUID, charge_id: UUID | None, actual_tokens: int | None
    ) -> None:
        if charge_id is None or actual_tokens is None:
            return
        state = self._state(endpoint_id)
        with state.condition:
            for charge in state.tokens:
                if charge.id == charge_id:
                    charge.tokens = actual_tokens
                    break
            state.condition.notify_all()

    def _release(self, endpoint_id: UUID) -> None:
        state = self._state(endpoint_id)
        with state.condition:
            if state.active <= 0:
                raise RuntimeError("endpoint concurrency permit underflow")
            state.active -= 1
            state.condition.notify_all()
        self._emit(LimiterEvent("released", endpoint_id))

    def close(self) -> None:
        with self._states_lock:
            self._closed = True
            states = list(self._states.values())
        for state in states:
            with state.condition:
                state.condition.notify_all()

    def _emit(self, event: LimiterEvent) -> None:
        if self._observer is not None:
            self._observer(event)


def is_retryable(error: BaseException) -> bool:
    return isinstance(error, (TimeoutError, NetworkError, RateLimitError, ProviderServerError))


def parse_retry_after(value: str | None, clock: Clock) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - clock.now_utc()).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class RetryPolicy:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        random_source: random.Random | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        base_seconds: float = 1.0,
        max_seconds: float = 60.0,
        jitter_ratio: float = 0.2,
    ) -> None:
        self.clock = clock or SystemClock()
        self.random = random_source or random.Random()
        self.sleeper = sleeper
        self.base = base_seconds
        self.maximum = max_seconds
        self.jitter = jitter_ratio

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        specified = parse_retry_after(retry_after, self.clock)
        if specified is not None:
            return specified
        raw = min(self.maximum, self.base * (2 ** max(0, attempt - 1)))
        jitter = float(self.random.uniform(-self.jitter, self.jitter))
        return float(max(0.0, raw * (1 + jitter)))

    def sleep(self, seconds: float, cancelled: threading.Event | None = None) -> None:
        deadline = self.clock.monotonic() + seconds
        while True:
            if cancelled is not None and cancelled.is_set():
                raise CancelledError()
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return
            self.sleeper(min(remaining, 0.1))


class ProviderCallExecutor:
    def __init__(
        self,
        limiter: CentralEndpointLimiter,
        retry_policy: RetryPolicy,
        observer: Observer | None = None,
    ) -> None:
        self.limiter = limiter
        self.retry_policy = retry_policy
        self.observer = observer

    def execute(
        self,
        endpoint: EndpointRecord,
        call: Callable[[], T],
        *,
        estimated_tokens: int = 0,
        usage_tokens: Callable[[T], int | None] | None = None,
        cancelled: threading.Event | None = None,
        retry_after: Callable[[BaseException], str | None] | None = None,
    ) -> T:
        for attempt in range(1, endpoint.data.max_retries + 2):
            log_event(
                _LOG,
                logging.DEBUG,
                "provider_attempt_started",
                endpoint_id=endpoint.id,
                attempt=attempt,
                max_retries=endpoint.data.max_retries,
            )
            with self.limiter.acquire(
                endpoint, estimated_tokens=estimated_tokens, cancelled=cancelled
            ) as permit:
                try:
                    result = call()
                except BaseException as exc:
                    permit.reconcile(None)
                    if not is_retryable(exc) or attempt > endpoint.data.max_retries:
                        log_event(
                            _LOG,
                            logging.ERROR,
                            "provider_call_failed",
                            endpoint_id=endpoint.id,
                            attempt=attempt,
                            exception_type=type(exc).__name__,
                        )
                        raise
                    delay = self.retry_policy.delay(
                        attempt, retry_after(exc) if retry_after is not None else None
                    )
                    self._emit(
                        LimiterEvent(
                            "retry_wait", endpoint.id, attempt=attempt, delay_seconds=delay
                        )
                    )
                    log_event(
                        _LOG,
                        logging.WARNING,
                        "provider_retry_scheduled",
                        endpoint_id=endpoint.id,
                        attempt=attempt,
                        delay_ms=round(delay * 1000, 3),
                        exception_type=type(exc).__name__,
                    )
                else:
                    permit.reconcile(usage_tokens(result) if usage_tokens is not None else None)
                    log_event(
                        _LOG,
                        logging.DEBUG,
                        "provider_attempt_completed",
                        endpoint_id=endpoint.id,
                        attempt=attempt,
                    )
                    return result
            self.retry_policy.sleep(delay, cancelled)
        raise RuntimeError("unreachable retry loop")

    def _emit(self, event: LimiterEvent) -> None:
        if self.observer is not None:
            self.observer(event)
