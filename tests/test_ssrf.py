"""SSRF suite: every private/reserved range — including the easily-missed
IPv4-mapped IPv6 and CGNAT cases — must be rejected before any connection is
attempted."""

from __future__ import annotations

import pytest

from theloom.documents.ssrf import (
    SsrfError,
    guard_url,
    resolve_and_validate,
    validate_url,
)

BLOCKED_LITERAL_URLS = [
    "http://127.0.0.1:8080/x",  # loopback
    "http://10.0.0.1/x",  # RFC 1918
    "http://172.16.0.1/x",  # RFC 1918
    "http://192.168.1.1/x",  # RFC 1918
    "http://169.254.169.254/latest/meta-data",  # link-local / cloud metadata
    "http://100.64.0.1/x",  # CGNAT shared space
    "http://192.0.2.1/x",  # TEST-NET-1
    "http://198.51.100.7/x",  # TEST-NET-2
    "http://203.0.113.9/x",  # TEST-NET-3
    "http://0.0.0.0/x",  # unspecified
    "http://[::1]/x",  # IPv6 loopback
    "http://[fe80::1]/x",  # IPv6 link-local
    "http://[fc00::1]/x",  # IPv6 ULA
    "http://[::ffff:127.0.0.1]/x",  # IPv4-mapped loopback
    "http://[::ffff:10.0.0.1]/x",  # IPv4-mapped RFC 1918
    "http://[::ffff:100.64.0.1]/x",  # IPv4-mapped CGNAT
    "http://[2001:db8::1]/x",  # IPv6 documentation range
]


class TestSchemeAllowlist:
    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://x/y", "gopher://x", "data:text/plain,hi"]
    )
    def test_non_http_rejected(self, url: str) -> None:
        with pytest.raises(SsrfError, match="only http: and https: are allowed"):
            validate_url(url)

    def test_error_message_matches_reference(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            validate_url("file:///etc/passwd")
        assert str(excinfo.value) == (
            "Unsupported protocol: file: - only http: and https: are allowed"
        )

    def test_http_and_https_allowed(self) -> None:
        assert validate_url("http://example.com/a") == "example.com"
        assert validate_url("https://example.com/a") == "example.com"


class TestBlockedAddresses:
    @pytest.mark.parametrize("url", BLOCKED_LITERAL_URLS)
    def test_literal_rejected_without_any_network_io(self, url: str) -> None:
        with pytest.raises(SsrfError, match="private or reserved address"):
            guard_url(url)

    def test_public_literal_allowed(self) -> None:
        validate_url("http://8.8.8.8/x")  # does not raise

    def test_reference_error_message(self) -> None:
        with pytest.raises(SsrfError) as excinfo:
            guard_url("http://127.0.0.1/x")
        assert str(excinfo.value) == "Access denied: URL points to a private or reserved address"


class TestResolutionCheck:
    def test_hostname_resolving_to_loopback_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(SsrfError, match="private or reserved address"):
            resolve_and_validate("rebind.example.com")

    def test_mixed_records_reject_if_any_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(SsrfError, match="private or reserved address"):
            resolve_and_validate("mixed.example.com")

    def test_all_global_records_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert resolve_and_validate("example.com") == ["93.184.216.34"]

    def test_resolution_failure_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(SsrfError, match="Failed to resolve URL host"):
            resolve_and_validate("nonexistent.invalid")
