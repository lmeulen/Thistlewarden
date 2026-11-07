from __future__ import annotations

import base64
import os
import threading
import time
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.api.errors import RateLimitError
from evaluator.endpoints import EndpointService
from evaluator.rate_limits import CentralEndpointLimiter, ProviderCallExecutor, RetryPolicy

pytestmark = pytest.mark.integration


def test_all_provider_call_types_share_one_limiter_and_concurrency_budget(tmp_path: Path) -> None:
    app = create_app(
        env_file=None,
        settings_environment={
            "DATABASE_PATH": str(tmp_path / "limiter.db"),
            "ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "LOG_LEVEL": "WARNING",
        },
        initialize_database=True,
    )
    target: ProviderCallExecutor = app.extensions["target_call_executor"]
    judge: ProviderCallExecutor = app.extensions["judge_call_executor"]
    connection: ProviderCallExecutor = app.extensions["connection_test_call_executor"]
    assert target is judge is connection is app.extensions["provider_call_executor"]
    assert target.limiter is app.extensions["endpoint_limiter"]
    endpoints: EndpointService = app.extensions["endpoint_service"]
    endpoint = endpoints.create(
        {
            "name": "Shared",
            "completions_url": "https://mock.test/chat",
            "max_concurrency": 1,
            "max_retries": 0,
        }
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_call() -> str:
        first_entered.set()
        assert release_first.wait(1)
        return "target"

    def second_call() -> str:
        second_entered.set()
        return "judge"

    first = threading.Thread(target=lambda: target.execute(endpoint, first_call))
    second = threading.Thread(target=lambda: judge.execute(endpoint, second_call))
    first.start()
    assert first_entered.wait(1)
    second.start()
    time.sleep(0.03)
    assert not second_entered.is_set()
    release_first.set()
    first.join(1)
    second.join(1)
    assert second_entered.is_set()
    app.extensions["shutdown_services"]()


def test_rate_limit_retry_after_then_success_emits_two_attempts() -> None:
    endpoint_service_events = []
    retry_events = []
    sleeps = []
    policy = RetryPolicy(jitter_ratio=0)
    policy.sleep = lambda seconds, cancelled=None: sleeps.append(seconds)  # type: ignore[method-assign]
    executor = ProviderCallExecutor(
        CentralEndpointLimiter(observer=endpoint_service_events.append),
        policy,
        retry_events.append,
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    from evaluator.endpoints import EndpointInput, EndpointRecord

    now = datetime.now(UTC)
    endpoint = EndpointRecord(
        uuid4(),
        EndpointInput(name="Retry", completions_url="https://mock.test", max_retries=1),
        None,
        now,
        now,
    )
    calls = 0

    def call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError()
        return "ok"

    assert (
        executor.execute(
            endpoint,
            call,
            retry_after=lambda error: "2.5",
        )
        == "ok"
    )
    assert calls == 2 and sleeps == [2.5]
    assert [event.kind for event in endpoint_service_events].count("acquired") == 2
    assert retry_events[0].kind == "retry_wait"
