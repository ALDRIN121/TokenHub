"""Hermes data is found under every install location and named profile."""

from pathlib import Path

from tokenhub.connectors.hermes.connector import HermesConnector
from tokenhub.connectors.protocol import DiscoveryContext


def context(home: Path, *, search_roots: tuple[Path, ...] = (),
            environment: dict[str, str] | None = None) -> DiscoveryContext:
    return DiscoveryContext(
        home=home,
        environment=environment or {},
        which=lambda _: r"C:\Users\aldrin\AppData\Local\hermes\hermes.exe",
        search_roots=search_roots,
    )


def test_windows_local_appdata_root_without_a_dot_is_discovered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    root = local / "hermes"
    root.mkdir(parents=True)
    (root / "state.db").write_text("synthetic database")

    result = HermesConnector().detect(context(home, search_roots=(local,)))

    assert "executable_on_path" in result.evidence_codes
    assert "state_database_found" in result.evidence_codes
    assert len(result.sources) == 1
    source = HermesConnector().discover_sources(context(home, search_roots=(local,)))[0]
    assert source.canonical_path == root / "state.db"
    assert source.approved_root == root


def test_named_profiles_are_separate_approvable_sources(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".hermes"
    root.mkdir(parents=True)
    (root / "state.db").write_text("default")
    work = root / "profiles" / "work"
    work.mkdir(parents=True)
    (work / "state.db").write_text("work profile")

    sources = HermesConnector().discover_sources(context(home))

    assert len(sources) == 2
    by_name = {source.display_name: source for source in sources}
    assert set(by_name) == {"Hermes state database", "Hermes state database (work)"}
    assert by_name["Hermes state database (work)"].approved_root == work
    assert by_name["Hermes state database"].approved_root == root
    # Separate roots means a profile can be approved without the default one.
    assert by_name["Hermes state database (work)"].source_id != by_name["Hermes state database"].source_id


def test_empty_home_stub_does_not_hide_data_on_another_drive(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    drive = tmp_path / "D"
    redirected = drive / "Users" / "aldrin"
    root = redirected / "hermes"
    root.mkdir(parents=True)
    (root / "state.db").write_text("real database")

    sources = HermesConnector().discover_sources(context(home, search_roots=(redirected,)))

    assert [source.canonical_path for source in sources] == [root / "state.db"]


def test_repeated_search_roots_do_not_duplicate_a_source(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".hermes"
    root.mkdir(parents=True)
    (root / "state.db").write_text("database")

    sources = HermesConnector().discover_sources(context(home, search_roots=(home, home)))

    assert len(sources) == 1


def test_channel_suffixed_root_is_discovered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    root = local / "hermes-dev"
    root.mkdir(parents=True)
    (root / "state.db").write_text("dev channel database")

    sources = HermesConnector().discover_sources(context(home, search_roots=(local,)))

    assert [source.canonical_path for source in sources] == [root / "state.db"]


def test_msix_package_localcache_root_is_discovered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "AppData" / "Local"
    root = (local / "Packages" / "NousResearch.Hermes_abc123"
            / "LocalCache" / "Local" / "hermes")
    root.mkdir(parents=True)
    (root / "state.db").write_text("packaged database")

    sources = HermesConnector().discover_sources(context(home, search_roots=(local,)))

    assert [source.canonical_path for source in sources] == [root / "state.db"]


def test_suffixed_dotted_root_is_discovered_on_posix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".hermes-dev"
    root.mkdir(parents=True)
    (root / "state.db").write_text("dev channel database")

    sources = HermesConnector().discover_sources(context(home))

    assert [source.canonical_path for source in sources] == [root / "state.db"]

