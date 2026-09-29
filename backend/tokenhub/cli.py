"""Commands for the local TokenHub dashboard and its background server."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from collections.abc import Sequence

from tokenhub.runtime.manager import RuntimeManager
from tokenhub.server import run_server
from tokenhub.settings import TokenHubSettings


def loopback_url(settings: TokenHubSettings) -> str:
    """The only URL this server is reachable at."""
    host = "[::1]" if settings.host == "::1" else settings.host
    return f"http://{host}:{settings.port}/"


def main(argv: Sequence[str] | None = None) -> int:
    """Start or manage the local dashboard; start opens it in a browser."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "_serve":
        if len(args) != 2:
            raise ValueError("internal server requires one port")
        token = os.environ.pop("TOKENHUB_INTERNAL_CONTROL_TOKEN")
        run_server(TokenHubSettings(port=int(args[1])), token)
        return 0

    parser = argparse.ArgumentParser(prog="tokenhub")
    parser.add_argument(
        "command", nargs="?", choices=("start", "status", "open", "stop"), default="start"
    )
    parser.add_argument("--port", type=int, help="loopback port for a new server")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser after start")
    options = parser.parse_args(args)
    manager = RuntimeManager(TokenHubSettings().data_directory)
    try:
        if options.command == "stop":
            print("Token Hub stopped" if manager.stop() else "Token Hub is not running")
            return 0
        if options.command == "status":
            url = manager.status()
            print(url if url else "Token Hub is not running")
            return 0 if url else 1
        if options.command == "open":
            url = manager.status()
            if url is None:
                print("Token Hub is not running; run tokenhub start")
                return 1
        else:
            url = manager.start(options.port)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Token Hub: {error}", file=sys.stderr)
        return 1

    print(url)
    if options.command == "open" or not options.no_open:
        try:
            webbrowser.open(url)
        except Exception as error:  # noqa: BLE001 - browser implementations vary by platform
            print(f"Could not open browser: {error}. Open {url}", file=sys.stderr)
    return 0
