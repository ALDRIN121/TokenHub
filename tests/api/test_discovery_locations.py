"""The location report answers all three discovery questions without paths."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.api.conftest import ORIGIN, codex_id


def _report(client: TestClient) -> dict:
    response = client.get("/api/v1/discovery/locations")
    assert response.status_code == 200
    return response.json()


def test_location_report_states_origin_validity_and_import_status(
    client: TestClient, tmp_path: Path
) -> None:
    report = _report(client)
    codex = next(
        provider for provider in report["providers"] if provider["connector_id"] == "codex-local"
    )

    assert codex["location_count"] == 1
    assert codex["importable_count"] == 1
    assert codex["pending_count"] == 1
    assert codex["imported_count"] == 0
    location = codex["locations"][0]
    assert location["origin"] == "home_default"
    assert location["evidence"] == "known_root_exists"
    assert location["state"] == "valid"
    assert location["markers"] == ["sessions"]
    assert location["already_imported"] is False
    assert str(tmp_path) not in str(report)


def test_report_marks_a_location_imported_after_its_events_are_ingested(
    client: TestClient,
) -> None:
    source_id = codex_id(client)
    assert client.post(f"/api/v1/sources/{source_id}/approve", headers=ORIGIN).status_code == 200
    assert (
        client.post(f"/api/v1/sources/{source_id}/rescan", headers=ORIGIN).json()["inserted_events"]
        == 1
    )

    codex = next(
        provider
        for provider in _report(client)["providers"]
        if provider["connector_id"] == "codex-local"
    )

    assert codex["imported_count"] == 1
    assert codex["pending_count"] == 0
    assert codex["locations"][0]["already_imported"] is True
    assert codex["locations"][0]["imported_events"] == 1


def test_report_lists_an_operator_location_as_a_second_source(
    client: TestClient, api_app
) -> None:
    home = api_app.state.container.settings.home_directory
    archive = home.parent / "archive"
    (archive / ".codex" / "sessions").mkdir(parents=True)
    (archive / ".codex" / "sessions" / "rollout.jsonl").write_text("{}\n")
    context = api_app.state.container.discovery_context
    api_app.state.container.discovery_context = context.__class__(
        context.home,
        context.environment,
        context.which,
        context.search_roots,
        locations={"codex-local": (archive / ".codex",)},
    )
    api_app.state.container.execute("discover", None)

    codex = next(
        provider
        for provider in _report(client)["providers"]
        if provider["connector_id"] == "codex-local"
    )

    assert codex["location_count"] == 2
    assert [location["origin"] for location in codex["locations"]] == [
        "user_config",
        "home_default",
    ]
    assert codex["pending_count"] == 2
