from __future__ import annotations

import socket
from pathlib import Path
from uuid import uuid4

import pytest

from evaluator.api.errors import ProviderClientError
from evaluator.endpoints import EndpointInput, EndpointRecord
from evaluator.network_security import is_restricted_address, validate_destination
from evaluator.runtime import InstanceLock
from evaluator.time import utc_now
from evaluator.web_security import is_allowed_host, is_allowed_origin


def endpoint(url: str, *, trusted: bool = False) -> EndpointRecord:
    data = EndpointInput(
        name="P",
        completions_url=url,
        trusted_internal_http=trusted,
        security_warning_confirmed=trusted,
    )
    now = utc_now()
    return EndpointRecord(uuid4(), data, None, now, now)


@pytest.mark.parametrize("host", ["localhost", "localhost:5000", "127.0.0.1:5000", "[::1]:5000"])
def test_local_hosts_are_allowed(host: str) -> None:
    assert is_allowed_host(host)


@pytest.mark.parametrize("host", ["evil.test", "127.0.0.1.evil.test", "0.0.0.0:5000", "[::]:5000"])
def test_nonlocal_hosts_are_rejected(host: str) -> None:
    assert not is_allowed_host(host)


def test_origin_policy_is_local_only() -> None:
    assert is_allowed_origin("http://127.0.0.1:5000")
    assert is_allowed_origin("http://[::1]:5000")
    assert not is_allowed_origin("https://attacker.test")


@pytest.mark.parametrize(
    "value", ["127.0.0.1", "::1", "169.254.169.254", "10.0.0.1", "fc00::1", "0.0.0.0", "224.0.0.1"]
)
def test_restricted_address_ranges(value: str) -> None:
    assert is_restricted_address(value)


def test_destination_rechecks_dns_and_blocks_mixed_public_private() -> None:
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
    ]
    with pytest.raises(ProviderClientError):
        validate_destination(
            endpoint("https://example.test/v1"),
            "https://example.test/v1",
            resolver=lambda *args: records,
        )


def test_explicit_trusted_endpoint_allows_private_destination() -> None:
    records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))]
    validate_destination(
        endpoint("https://internal.test/v1", trusted=True),
        "https://internal.test/v1",
        resolver=lambda *args: records,
    )


def test_instance_lock_rejects_second_owner_and_releases(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "app.lock")
    second = InstanceLock(tmp_path / "app.lock")
    first.acquire()
    try:
        from evaluator.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
    assert (tmp_path / "app.lock").stat().st_mode & 0o777 in {0o600, 0o666}
