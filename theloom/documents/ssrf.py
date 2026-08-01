"""SSRF hardening for ingest-url.

Every resolved address must be GLOBAL per the stdlib ``ipaddress`` registry
(rejects loopback, private, link-local, CGNAT shared space,
TEST-NET/documentation, multicast, reserved, unspecified), and IPv4-mapped
IPv6 literals ([::ffff:127.0.0.1]) are decoded before the check so they cannot
smuggle a loopback/RFC-1918 target past validation. Every redirect target is
re-validated the same way. Residual TOCTOU: the fetch re-resolves DNS after
validation; a rebinding attacker needs to win that race.

Error strings are specific to each rejection reason and form the ingest-url
error contract.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB


class SsrfError(Exception):
    """A blocked URL. Message text is part of the CLI error contract."""


def _address_is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return not address.is_global


def validate_url(url: str) -> str:
    """Scheme + literal-host validation; returns the hostname."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SsrfError(
            f"Unsupported protocol: {parsed.scheme}: - only http: and https: are allowed"
        )
    host = parsed.hostname
    if not host:
        raise SsrfError("Invalid URL: no hostname")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return host  # not an IP literal; resolution check happens next
    if _address_is_blocked(literal):
        raise SsrfError("Access denied: URL points to a private or reserved address")
    return host


def resolve_and_validate(host: str) -> list[str]:
    """Resolve the host and require EVERY address to be globally routable
    (a single blocked A/AAAA record rejects the URL — rebinding defense)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SsrfError(f"Failed to resolve URL host: {host}") from exc
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise SsrfError(f"Failed to resolve URL host: {host}")
    for raw in addresses:
        if _address_is_blocked(ipaddress.ip_address(raw)):
            raise SsrfError("Access denied: URL points to a private or reserved address")
    return addresses


def guard_url(url: str) -> None:
    """Full pre-fetch validation: scheme, literal, and DNS resolution."""
    host = validate_url(url)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        resolve_and_validate(host)


def fetch_url(url: str, *, transport: Any | None = None) -> tuple[str, str]:
    """Fetch with SSRF validation on the initial URL and every redirect hop.
    Returns (final_url, body_text). Raises SsrfError on blocked targets."""
    import httpx

    current = url
    with httpx.Client(
        timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False, transport=transport
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            guard_url(current)
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise SsrfError(f"Failed to fetch URL: {exc}") from exc
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise SsrfError("Redirect without Location header")
                current = str(response.next_request.url) if response.next_request else location
                continue
            if response.status_code >= 400:
                raise SsrfError(f"Failed to fetch URL: HTTP {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise SsrfError(
                    f"Response exceeds maximum size of {MAX_RESPONSE_BYTES // (1024 * 1024)} MB"
                )
            return current, response.text
    raise SsrfError("Too many redirects")
