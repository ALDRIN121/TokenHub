"""Settings defaults/validation and CLI startup wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from tokenhub.settings import DEFAULT_HOST, DEFAULT_PORT, TokenHubSettings


def test_defaults_bind_loopback_and_port_7432() -> None:
    settings = TokenHubSettings()
    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 7432
    assert settings.host == "127.0.0.1"
    assert settings.port == 7432


def test_default_data_directory_is_tokenhub_under_the_home_directory(tmp_path: Path) -> None:
    explicit = TokenHubSettings(home_directory=tmp_path / "home")
    assert explicit.data_directory == tmp_path / "home" / ".tokenhub"
    assert explicit.home_directory == tmp_path / "home"

    implicit = TokenHubSettings()
    assert implicit.home_directory == Path.home()
    assert implicit.data_directory == Path.home() / ".tokenhub"


def test_explicit_data_directory_is_used_verbatim(tmp_path: Path) -> None:
    settings = TokenHubSettings(home_directory=tmp_path / "home", data_directory=tmp_path / "store")
    assert settings.data_directory == tmp_path / "store"


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "attacker.example",
        "127.0.0.1@attacker",
        "LOCALHOST",
        "",
        "127.0.0.1:7432",
        "localhost:7432",
        "[::1]",
        " ",
        "192.168.1.10",
    ],
)
def test_settings_reject_non_loopback_bind_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        TokenHubSettings(host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_settings_accept_the_three_loopback_bind_hosts(host: str) -> None:
    assert TokenHubSettings(host=host).host == host


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_settings_reject_out_of_range_ports(port: int) -> None:
    with pytest.raises(ValueError, match="port"):
        TokenHubSettings(port=port)


@pytest.mark.parametrize("port", [1, 7432, 65535])
def test_settings_accept_in_range_ports(port: int) -> None:
    assert TokenHubSettings(port=port).port == port


def test_loopback_url_brackets_ipv6_and_keeps_defaults() -> None:
    from tokenhub import cli

    assert cli.loopback_url(TokenHubSettings(host="127.0.0.1")) == "http://127.0.0.1:7432/"
    assert cli.loopback_url(TokenHubSettings(host="localhost")) == "http://localhost:7432/"
    assert cli.loopback_url(TokenHubSettings(host="::1")) == "http://[::1]:7432/"
    assert cli.loopback_url(TokenHubSettings(host="::1", port=9000)) == "http://[::1]:9000/"


def test_cli_main_runs_uvicorn_on_loopback_without_proxy_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenhub import cli

    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    cli.main()
    assert len(calls) == 1
    app, kwargs = calls[0]
    assert isinstance(app, FastAPI)
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 7432,
        "proxy_headers": False,
        "access_log": False,
    }
