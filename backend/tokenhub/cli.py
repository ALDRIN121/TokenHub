"""Command-line entrypoint for TokenHub.

Serves the API and the built UI on loopback only. Providers are discovered at
runtime from the real home directory, documented environment overrides, and
PATH — nothing about this machine is compiled into the package.

The server never opens a browser window: the spec forbids the app from reaching
outside its own process, and the loopback URL is printed by uvicorn's default
startup log. Open it yourself, or use :func:`loopback_url`.
"""

from __future__ import annotations

import uvicorn

from tokenhub.app import create_app
from tokenhub.settings import TokenHubSettings


def loopback_url(settings: TokenHubSettings) -> str:
    """The only URL this server is reachable at."""
    host = "[::1]" if settings.host == "::1" else settings.host
    return f"http://{host}:{settings.port}/"


def main() -> None:
    """Serve the API and the built UI on the loopback interface."""
    settings = TokenHubSettings()
    print(
        f"Starting TokenHub on {loopback_url(settings)} (loopback only, Ctrl+C to stop)"
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # No proxy is trusted, and local request lines are not worth logging.
        proxy_headers=False,
        access_log=False,
    )
