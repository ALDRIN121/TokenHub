"""The control client authenticates responses from a direct loopback socket."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tokenhub.runtime.client import probe, request_stop
from tokenhub.runtime.control import proof
from tokenhub.runtime.instance import InstanceRecord

TOKEN = "a" * 64


@contextmanager
def control_server(*, forged=False, ready_status=200):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            challenge = self.headers.get("X-TokenHub-Challenge")
            seen.append(("ready", challenge, self.headers.get("Origin")))
            if self.path != "/api/v1/_runtime/ready":
                self.send_error(404)
                return
            body = json.dumps({"proof": "0" * 64 if forged else proof(TOKEN, f"ready:{challenge}")}).encode()
            self.send_response(ready_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            nonce = self.headers.get("X-TokenHub-Nonce")
            seen.append(("stop", nonce, self.headers.get("Origin"), self.headers.get("X-TokenHub-Proof")))
            self.send_response(202 if self.path == "/api/v1/_runtime/stop" else 404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_probe_uses_fresh_challenge_and_accepts_only_correct_proof():
    with control_server() as (port, seen):
        record = InstanceRecord(123, port, TOKEN)
        assert probe(record)
        assert probe(record)
        assert len({row[1] for row in seen}) == 2
        assert all(len(row[1]) == 64 for row in seen)
    with control_server(forged=True) as (port, _):
        assert not probe(InstanceRecord(123, port, TOKEN))
    with control_server(ready_status=503) as (port, _):
        assert not probe(InstanceRecord(123, port, TOKEN))


def test_request_stop_sends_fresh_proof_and_origin():
    with control_server() as (port, seen):
        record = InstanceRecord(123, port, TOKEN)
        assert request_stop(record)
        assert request_stop(record)
        assert len({row[1] for row in seen}) == 2
        for _, nonce, origin, actual in seen:
            assert origin == f"http://127.0.0.1:{port}"
            assert actual == proof(TOKEN, f"stop:{nonce}")


def test_connection_refusal_is_not_proof():
    with control_server() as (port, _):
        pass
    record = InstanceRecord(123, port, TOKEN)
    assert not probe(record)
    assert not request_stop(record)
