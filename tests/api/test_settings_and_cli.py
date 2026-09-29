"""Settings defaults/validation and CLI startup wiring."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def test_cli_start_defaults_to_background_and_opens_browser(monkeypatch, capsys) -> None:
    from tokenhub import cli

    starts, opens = [], []
    manager = SimpleNamespace(start=lambda port=None: starts.append(port) or "http://127.0.0.1:7432/")
    monkeypatch.setattr(cli, "RuntimeManager", lambda _: manager)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opens.append(url) or True)
    assert cli.main([]) == 0
    assert starts == [None]
    assert opens == ["http://127.0.0.1:7432/"]
    assert "http://127.0.0.1:7432/" in capsys.readouterr().out
    assert cli.main(["start", "--port", "9000", "--no-open"]) == 0
    assert starts == [None, 9000]
    assert len(opens) == 1


def test_cli_status_open_and_stop(monkeypatch, capsys) -> None:
    from tokenhub import cli

    opens = []
    manager = SimpleNamespace(
        status=lambda: "http://127.0.0.1:9000/",
        stop=lambda: True,
    )
    monkeypatch.setattr(cli, "RuntimeManager", lambda _: manager)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opens.append(url) or True)
    assert cli.main(["status"]) == 0
    assert cli.main(["open"]) == 0
    assert opens == ["http://127.0.0.1:9000/"]
    assert cli.main(["stop"]) == 0
    assert "Token Hub stopped" in capsys.readouterr().out


def test_cli_stopped_open_and_browser_failure(monkeypatch, capsys) -> None:
    from tokenhub import cli

    manager = SimpleNamespace(
        status=lambda: None,
        stop=lambda: False,
        start=lambda port=None: "http://127.0.0.1:7432/",
    )
    monkeypatch.setattr(cli, "RuntimeManager", lambda _: manager)
    monkeypatch.setattr(cli.webbrowser, "open", lambda _: False)
    assert cli.main(["open"]) == 1
    assert "run tokenhub start" in capsys.readouterr().out
    assert cli.main(["status"]) == 1
    assert cli.main(["stop"]) == 0
    assert cli.main(["start"]) == 0
    assert "http://127.0.0.1:7432/" in capsys.readouterr().out


def test_hidden_child_entrypoint_consumes_token(monkeypatch):
    from tokenhub import cli

    calls = []
    monkeypatch.setenv("TOKENHUB_INTERNAL_CONTROL_TOKEN", "a" * 64)
    monkeypatch.setattr(cli, "run_server", lambda settings, token: calls.append((settings.port, token)))
    assert cli.main(["_serve", "9000"]) == 0
    assert calls == [(9000, "a" * 64)]
    assert "TOKENHUB_INTERNAL_CONTROL_TOKEN" not in os.environ
