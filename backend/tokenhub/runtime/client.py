"""Direct loopback challenge client for managed TokenHub instances."""

from __future__ import annotations

import http.client
import json
import secrets
import urllib.error
import urllib.request

from tokenhub.runtime.control import proof
from tokenhub.runtime.instance import InstanceRecord

_TIMEOUT_SECONDS = 0.5


def _opener() -> urllib.request.OpenerDirector:
    # A local process must never be reached through configured HTTP proxies.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _origin(record: InstanceRecord) -> str:
    return f"http://127.0.0.1:{record.port}"


def probe(record: InstanceRecord) -> bool:
    """Prove that the process on the recorded port has our private token."""
    challenge = secrets.token_hex(32)
    request = urllib.request.Request(
        f"{_origin(record)}/api/v1/_runtime/ready",
        headers={"X-TokenHub-Challenge": challenge},
    )
    try:
        with _opener().open(request, timeout=_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return False
            body = json.load(response)
        actual = body["proof"]
        return isinstance(actual, str) and secrets.compare_digest(
            actual, proof(record.token, f"ready:{challenge}")
        )
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return False


def request_stop(record: InstanceRecord) -> bool:
    """Ask a previously verified server to stop without signaling its PID."""
    nonce = secrets.token_hex(32)
    request = urllib.request.Request(
        f"{_origin(record)}/api/v1/_runtime/stop",
        headers={
            "Origin": _origin(record),
            "X-TokenHub-Nonce": nonce,
            "X-TokenHub-Proof": proof(record.token, f"stop:{nonce}"),
        },
        method="POST",
    )
    try:
        with _opener().open(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status == 202
    except (OSError, ValueError, http.client.HTTPException):
        return False
