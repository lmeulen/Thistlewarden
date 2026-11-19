"""Connect-attempt destination policy for provider HTTP calls."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from evaluator.api.errors import NetworkError, ProviderClientError
from evaluator.endpoints import EndpointRecord

Resolver = Callable[..., list[tuple[Any, ...]]]
_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}


def is_restricted_address(value: str) -> bool:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    return (
        address in _METADATA_ADDRESSES
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def validate_destination(
    endpoint: EndpointRecord,
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ProviderClientError("Provider destination is invalid.")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderClientError("Provider destination contains forbidden user information.")
    try:
        records = resolver(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except OSError as exc:
        raise NetworkError("Provider host could not be resolved safely.") from exc
    addresses = {str(record[4][0]) for record in records}
    if not addresses:
        raise NetworkError("Provider host has no addresses.")
    restricted = [address for address in addresses if is_restricted_address(address)]
    hostname = parsed.hostname.casefold()
    loopback_name = hostname == "localhost"
    only_loopback = all(ipaddress.ip_address(x.split("%", 1)[0]).is_loopback for x in addresses)
    trusted = endpoint.data.trusted_internal_http and endpoint.data.security_warning_confirmed
    if restricted and not ((loopback_name and only_loopback) or trusted):
        raise ProviderClientError(
            "Provider host resolves to a restricted internal or metadata address."
        )
