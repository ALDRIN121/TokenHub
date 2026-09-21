from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.settings import TokenHubSettings

from tests.service_support import token_record


@pytest.fixture
def api_app(tmp_path: Path) -> FastAPI:
    home = tmp_path / "home"
    source = home / ".codex" / "sessions" / "synthetic.jsonl"
    source.parent.mkdir(parents=True)
    source.write_bytes(token_record(1, input_tokens=100, output_tokens=25))
    (home / ".hermes").mkdir()
    (home / ".hermes" / "state.db").write_text("synthetic private database")
    (home / ".codex" / "auth.json").write_text("sk-synthetic-secret")
    app = create_app(TokenHubSettings(home_directory=home, data_directory=tmp_path / "data"))
    app.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    return app


@pytest.fixture
def client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app, base_url="http://127.0.0.1:7432") as http:
        yield http


def codex_id(client: TestClient) -> str:
    response = client.get("/api/v1/discovery")
    assert response.status_code == 200
    return response.json()["providers"][1]["sources"][0]["source_id"]


ORIGIN = {"origin": "http://127.0.0.1:7432"}
