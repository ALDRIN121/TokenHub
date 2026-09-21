"""FastAPI application factory for the local TokenHub server.

Two guards run before every route, in this order:

1. **Host** — only loopback host forms with a canonical port are accepted, so a
   DNS-rebinding page cannot reach the API. Applies to the static UI as well.
2. **Origin** — state-changing routes must carry the request's own loopback
   origin; a missing, delegated, or duplicated Origin is refused before any
   service sees the request.

Neither guard adds CORS headers, and forwarded-host/proto headers are never
consulted: the server is not behind a proxy by design.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from tokenhub.api.container import Container
from tokenhub.api.routes import router
from tokenhub.security.http import is_loopback_host, is_same_origin
from tokenhub.settings import TokenHubSettings

#: Built UI location; mounted only when a build exists, so API tests and a
#: source checkout do not need a frontend build.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})


def create_app(settings: TokenHubSettings | None = None) -> FastAPI:
    """Build the app. No database, migration, or discovery work happens here."""
    container = Container(settings if settings is not None else TokenHubSettings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        container.start()
        try:
            yield
        finally:
            container.stop()

    app = FastAPI(title="TokenHub", lifespan=lifespan)
    app.state.container = container

    @app.middleware("http")
    async def local_request_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        port = container.settings.port
        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or not is_loopback_host(hosts[0], port):
            return JSONResponse(status_code=400, content={"detail": "Invalid Host header"})
        if request.method not in _READ_ONLY_METHODS:
            origins = request.headers.getlist("origin")
            if len(origins) != 1 or not is_same_origin(origins[0], hosts[0], port):
                return JSONResponse(
                    status_code=403, content={"detail": "Invalid Origin header"}
                )
        return await call_next(request)

    app.include_router(router)
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app
