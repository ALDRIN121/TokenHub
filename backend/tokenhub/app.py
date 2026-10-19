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

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from tokenhub.api.container import Container
from tokenhub.api.routes import router
from tokenhub.runtime import RuntimeControl, runtime_router
from tokenhub.security.http import is_loopback_host, is_same_origin
from tokenhub.settings import TokenHubSettings

#: The built UI is package data so installed wheels and source builds serve it.
FRONTEND_DIST = Path(__file__).resolve().parent / "web"

_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})


def create_app(
    settings: TokenHubSettings | None = None,
    runtime_control: RuntimeControl | None = None,
) -> FastAPI:
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

    @app.middleware("http")
    async def conditional_api_cache(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Revalidate cached API reads with an ETag instead of resending them.

        The payload already carries ``data_version``, so an ingest that changes
        data produces a new body and therefore a new tag. A poll that finds
        nothing new costs a 304 and no JSON parse on the client.
        """
        response = await call_next(request)
        is_api_read = (
            request.method in _READ_ONLY_METHODS
            and request.url.path.startswith("/api/v1/")
            and response.status_code == 200
            and response.headers.get("content-type", "").startswith("application/json")
        )
        if not is_api_read:
            return response
        # BaseHTTPMiddleware hands back a streaming response; buffer the
        # (bounded, local) JSON body so the tag covers exactly what is sent.
        body = getattr(response, "body", None)
        if body is None:
            body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() != "content-length"}
        headers["ETag"] = etag
        headers["Cache-Control"] = "no-cache"
        return Response(content=body, status_code=response.status_code,
                        headers=headers, media_type=response.media_type)

    app.include_router(router)
    if runtime_control is not None:
        app.include_router(runtime_router(runtime_control))
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app
