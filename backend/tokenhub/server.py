"""Run a loopback server with an optional private shutdown callback."""

from __future__ import annotations

import uvicorn

from tokenhub.app import create_app
from tokenhub.runtime import RuntimeControl
from tokenhub.settings import TokenHubSettings


def run_server(settings: TokenHubSettings, control_token: str | None = None) -> None:
    """Serve until interrupted or an authorized local stop request arrives."""
    server: uvicorn.Server

    def shutdown() -> None:
        server.should_exit = True

    control = RuntimeControl(control_token, shutdown) if control_token else None
    app = create_app(settings, runtime_control=control)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
