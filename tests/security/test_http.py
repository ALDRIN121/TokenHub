"""Direct unit tests for the loopback Host / same-origin policy.

These call :mod:`tokenhub.security.http` as pure functions, without an HTTP
stack or the app middleware, so every accepted and rejected spelling is pinned
independently of FastAPI's header handling.
"""

from __future__ import annotations

import pytest
from tokenhub.security.http import (
    expected_origin,
    is_loopback_host,
    is_same_origin,
    parse_host_header,
)

PORT = 7432

ACCEPTED_HOSTS: tuple[tuple[str, tuple[str, int | None]], ...] = (
    ("localhost", ("localhost", None)),
    ("localhost:7432", ("localhost", PORT)),
    ("127.0.0.1", ("127.0.0.1", None)),
    ("127.0.0.1:7432", ("127.0.0.1", PORT)),
    ("[::1]", ("[::1]", None)),
    ("[::1]:7432", ("[::1]", PORT)),
)


@pytest.mark.parametrize(("header", "parsed"), ACCEPTED_HOSTS)
def test_accepted_host_forms_parse(header: str, parsed: tuple[str, int | None]) -> None:
    assert parse_host_header(header, PORT) == parsed
    assert is_loopback_host(header, PORT) is True


REJECTED_HOSTS: tuple[str, ...] = (
    "attacker.example",
    "localhost.attacker.example",
    "localhost.",
    "localhost@attacker.example",
    "attacker@localhost",
    "http://localhost",
    "127.1",
    "2130706433",
    "::1",
    "[::1]evil",
    "localhost:7432:99",
    "localhost:",
    "localhost:0007432",
    "localhost:0",
    "localhost:65536",
    " localhost",
    "localhost ",
    "localhost/path",
    "localhost?x",
    "localhost#x",
    "localhost,attacker",
    "localhost\r\nx-evil: yes",
    "",
    "[::1%lo0]",
    "LOCALHOST",
    "localhost\u00e9",
    "localhost:123456",
    "localhost:123456789",
    # Adversarial spellings that must never be treated as loopback.
    "localhost:7432@attacker.example",
    "attacker.example:7432",
    "127.0.0.1.evil",
    "localhost..",
    "127.0.0.1\x00",
    "127.0.0.1:7432\n",
    "localhost\t:7432",
    "[::1]:7432:0",
    "127.0.0.1:",
    ":7432",
    "//127.0.0.1",
    "127.0.0.1:07432",
    "127.0.0.1:99999",
    "127.0.0.1:74320",
    "127.000.000.001",
    "0.0.0.0:7432",
    "[0:0:0:0:0:0:0:1]",
    "localhost\x85",
    "Localhost",
    "[::1]:0",
    "[::1]:65536",
    "127.0.0.1:7432/../x",
)


@pytest.mark.parametrize("header", REJECTED_HOSTS)
def test_confusing_host_forms_are_refused(header: str) -> None:
    assert parse_host_header(header, PORT) is None
    assert is_loopback_host(header, PORT) is False


@pytest.mark.parametrize("header", [None, "", "   ", "\t"])
def test_missing_blank_or_whitespace_host_is_refused(header: str | None) -> None:
    assert parse_host_header(header, PORT) is None
    assert is_loopback_host(header, PORT) is False


@pytest.mark.parametrize("header", ["127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:0007432"])
def test_non_canonical_ports_are_refused_for_every_host(header: str) -> None:
    assert parse_host_header(header, PORT) is None


@pytest.mark.parametrize("header", ["localhost:1", "localhost:65535", "[::1]:1", "[::1]:65535"])
def test_boundary_ports_parse_without_a_configured_port(header: str) -> None:
    hostname, port = header.rsplit(":", 1)
    assert parse_host_header(header, None) == (hostname, int(port))


def test_configured_port_restricts_accepted_port() -> None:
    """A foreign port is refused only when a port is configured."""
    for header in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "127.0.0.1:7433"):
        assert parse_host_header(header, PORT) is None
        assert is_loopback_host(header, PORT) is False
    # The configured port itself and a portless host remain acceptable.
    assert parse_host_header("localhost:7432", PORT) == ("localhost", PORT)
    assert parse_host_header("localhost", PORT) == ("localhost", None)


def test_no_configured_port_accepts_any_valid_port() -> None:
    assert parse_host_header("localhost:8000", None) == ("localhost", 8000)
    assert parse_host_header("127.0.0.1:1", None) == ("127.0.0.1", 1)
    assert parse_host_header("[::1]:65535", None) == ("[::1]", 65535)
    # Out-of-range and non-canonical ports stay refused either way.
    assert parse_host_header("localhost:0", None) is None
    assert parse_host_header("localhost:65536", None) is None
    assert parse_host_header("localhost:0007432", None) is None


@pytest.mark.parametrize(
    ("host_header", "expected_port", "origin"),
    (
        ("localhost", PORT, "http://localhost:7432"),
        ("localhost:7432", PORT, "http://localhost:7432"),
        ("127.0.0.1:7432", PORT, "http://127.0.0.1:7432"),
        ("[::1]:7432", PORT, "http://[::1]:7432"),
        ("localhost", None, "http://localhost"),
        ("127.0.0.1", None, "http://127.0.0.1"),
        ("[::1]", None, "http://[::1]"),
    ),
)
def test_expected_origin_derives_from_the_request_host(
    host_header: str, expected_port: int | None, origin: str
) -> None:
    assert expected_origin(host_header, expected_port) == origin


@pytest.mark.parametrize("host_header", ["attacker.example", "localhost:8000", "", None])
def test_expected_origin_refused_for_invalid_host(host_header: str | None) -> None:
    assert expected_origin(host_header, PORT) is None


@pytest.mark.parametrize("host_header", ["localhost:7432", "127.0.0.1:7432", "[::1]:7432"])
def test_same_origin_accepts_the_requests_own_loopback_origin(host_header: str) -> None:
    assert is_same_origin(expected_origin(host_header, PORT), host_header, PORT) is True


REJECTED_ORIGINS: tuple[str, ...] = (
    "null",
    "https://127.0.0.1:7432",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:7432/",
    "http://127.0.0.1:7432/path",
    "http://user@127.0.0.1:7432",
    "http://127.0.0.1:7432 ",
    "  http://127.0.0.1:7432",
    "http://127.0.0.1:7432\r\nx-evil: yes",
    "http://127.0.0.1:7432http://127.0.0.1:7432",
    "http://127.0.0.1:7432,http://127.0.0.1:7432",
    "http://LOCALHOST:7432",
    "http://localhost:7432",
    "attacker.example",
    # Adversarial spellings that must never equal the request's own origin.
    "HTTP://127.0.0.1:7432",
    "http://127.0.0.1:7432\t",
    "file://127.0.0.1:7432",
    "http://[::1]:7432",
    "http://127.0.0.1:07432",
)


@pytest.mark.parametrize("origin", REJECTED_ORIGINS)
def test_same_origin_refuses_foreign_or_malformed_origin(origin: str) -> None:
    assert is_same_origin(origin, "127.0.0.1:7432", PORT) is False


@pytest.mark.parametrize("origin", [None, ""])
def test_same_origin_refuses_missing_origin(origin: str | None) -> None:
    assert is_same_origin(origin, "127.0.0.1:7432", PORT) is False
