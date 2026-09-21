"""Strict loopback Host and same-origin policy for the local TokenHub server.

Both checks are pure string functions so they can be unit-tested without an
HTTP stack:

* :func:`is_loopback_host` accepts only `localhost`, `127.0.0.1`, and `[::1]`,
  optionally with a canonical TCP port (`7432`, never `0007432`, `0`, or
  `65536`) that matches the server's configured port. Every other spelling —
  including DNS-rebinding shapes such as
  `localhost.attacker.example`, userinfo tricks, `http://localhost`, decimal
  IPs (`2130706433`), trailing dots, path/query/fragment suffixes, whitespace,
  header-injection line breaks, upper-case `LOCALHOST`, and a foreign port such
  as `:8000` — is refused.
* :func:`is_same_origin` requires the request's own loopback origin exactly:
  scheme `http`, the same host spelling the request arrived with, and the
  configured port. Delegated origins, `null`, `https`, and anything carrying
  userinfo or whitespace are refused.
"""

from __future__ import annotations

from typing import Final

LOOPBACK_HOSTNAMES: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "[::1]"})
_MIN_PORT: Final = 1
_MAX_PORT: Final = 65535


def _has_unsafe_characters(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 0x21 or ord(character) == 0x7F
        for character in value
    )


def parse_host_header(
    value: str | None, expected_port: int | None = None
) -> tuple[str, int | None] | None:
    """Return `(hostname, port)` for an allowed Host header, else ``None``.

    A port, when present, must equal *expected_port* (the configured server
    port); a bare hostname is always allowed.
    """
    if not isinstance(value, str) or not value:
        return None
    if not value.isascii() or _has_unsafe_characters(value):
        return None

    if value.startswith("["):
        bracket = value.find("]")
        if bracket == -1:
            return None
        hostname = value[: bracket + 1]
        remainder = value[bracket + 1 :]
    else:
        hostname, separator, port_text = value.partition(":")
        if ":" in port_text:  # ``a:b:c`` and bare ``::1`` are not host:port
            return None
        remainder = f":{port_text}" if separator else ""

    port: int | None = None
    if remainder:
        if not remainder.startswith(":"):
            return None
        digits = remainder[1:]
        if not digits.isdigit() or len(digits) > 5:
            return None
        if len(digits) > 1 and digits.startswith("0"):
            return None
        port = int(digits)
        if not _MIN_PORT <= port <= _MAX_PORT:
            return None
        if expected_port is not None and port != expected_port:
            return None

    if hostname not in LOOPBACK_HOSTNAMES:
        return None
    return hostname, port


def is_loopback_host(value: str | None, expected_port: int | None = None) -> bool:
    """Whether *value* is an acceptable loopback Host header."""
    return parse_host_header(value, expected_port) is not None


def expected_origin(
    host_header: str | None, expected_port: int | None = None
) -> str | None:
    """The single origin this request may claim, from its own Host header."""
    parsed = parse_host_header(host_header, expected_port)
    if parsed is None:
        return None
    hostname, port = parsed
    selected_port = port if port is not None else expected_port
    if selected_port is None:
        return f"http://{hostname}"
    return f"http://{hostname}:{selected_port}"


def is_same_origin(
    origin: str | None, host_header: str | None, expected_port: int | None = None
) -> bool:
    """Whether *origin* is exactly the request's own loopback origin."""
    if not isinstance(origin, str) or not origin:
        return False
    if not origin.isascii() or _has_unsafe_characters(origin):
        return False
    return origin == expected_origin(host_header, expected_port)
