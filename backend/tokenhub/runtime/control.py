"""Challenge proofs for identifying and stopping the managed local server."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


@dataclass(frozen=True, slots=True)
class RuntimeControl:
    token: str
    shutdown: Callable[[], None]


def proof(token: str, message: str) -> str:
    """Authenticate a purpose-prefixed message without exposing the token."""
    return hmac.new(bytes.fromhex(token), message.encode(), hashlib.sha256).hexdigest()


def _single_random_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) != 1:
        return None
    value = values[0]
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def runtime_router(control: RuntimeControl) -> APIRouter:
    """Expose control routes only on an explicitly managed app instance."""
    router = APIRouter(prefix="/api/v1/_runtime", include_in_schema=False)

    @router.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        challenge = _single_random_header(request, "x-tokenhub-challenge")
        if challenge is None:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(content={"proof": proof(control.token, f"ready:{challenge}")})

    @router.post("/stop")
    async def stop(request: Request) -> JSONResponse:
        nonce = _single_random_header(request, "x-tokenhub-nonce")
        supplied_proofs = request.headers.getlist("x-tokenhub-proof")
        if (
            nonce is None
            or len(supplied_proofs) != 1
            or not secrets.compare_digest(
                supplied_proofs[0], proof(control.token, f"stop:{nonce}")
            )
        ):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        control.shutdown()
        return JSONResponse(status_code=202, content={"status": "stopping"})

    return router
