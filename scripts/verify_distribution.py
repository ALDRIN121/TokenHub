"""Inspect and smoke-test installable TokenHub wheel and source archives."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import urllib.request
import venv
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

_MIGRATIONS = "tokenhub/database/migrations/versions/"


def _members(path: Path) -> list[str]:
    """Package-relative member names inside a wheel or source archive."""
    if path.suffix == ".whl":
        with ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [
                name.split("/", 1)[1]
                for name in archive.getnames()
                if "/" in name
            ]
        return [name.removeprefix("backend/") for name in members]
    raise ValueError(f"unsupported package archive: {path}")


def _read_member(path: Path, name: str) -> str:
    if path.suffix == ".whl":
        with ZipFile(path) as archive:
            return archive.read(name).decode("utf-8")
    with tarfile.open(path, "r:gz") as archive:
        # Source archives nest everything under a versioned top-level directory.
        for member in archive.getmembers():
            if member.name == name or member.name.endswith("/" + name):
                handle = archive.extractfile(member)
                if handle is not None:
                    return handle.read().decode("utf-8")
    raise KeyError(name)


def head_revision(path: Path) -> str:
    """Alembic head packaged in the archive, derived instead of hard-coded.

    A new migration used to break this check because the expected revision was
    a constant; walking the packaged revision graph keeps the smoke test
    honest without an edit on every migration.
    """
    revisions: set[str] = set()
    parents: set[str] = set()
    for name in _members(path):
        if not (name.startswith(_MIGRATIONS) and name.endswith(".py")):
            continue
        text = _read_member(path, name)
        revision = re.search(r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        parent = re.search(r'^down_revision[^=]*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if revision is not None:
            revisions.add(revision.group(1))
        if parent is not None:
            parents.add(parent.group(1))
    heads = revisions - parents
    assert len(heads) == 1, f"expected one packaged Alembic head, found {sorted(heads)}"
    return heads.pop()


def verify_archive(path: Path) -> None:
    """Reject an archive missing any runtime or distribution resource."""
    members = _members(path)

    assert "tokenhub/web/index.html" in members, "missing tokenhub/web/index.html"
    assert any(
        name.startswith("tokenhub/web/assets/") and name.endswith(".js") for name in members
    ), "missing compiled JavaScript"
    assert any(
        name.startswith("tokenhub/web/assets/") and name.endswith(".css") for name in members
    ), "missing compiled CSS"
    assert any(
        name.startswith(_MIGRATIONS) and name.endswith(".py") for name in members
    ), "missing packaged database migrations"
    assert any(name.endswith("/entry_points.txt") for name in members), "missing entry point"
    assert any(name == "LICENSE" or name.endswith("/LICENSE") for name in members), "missing LICENSE"


def _run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 60) -> str:
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def smoke_install(path: Path) -> None:
    """Install the artifact into a clean venv and exercise the real dashboard."""
    with tempfile.TemporaryDirectory(prefix="tokenhub-distribution-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.create(environment, with_pip=True)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        tokenhub = scripts / ("tokenhub.exe" if os.name == "nt" else "tokenhub")
        _run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", str(path.resolve())],
            timeout=180,
        )

        home = root / "home"
        home.mkdir()
        # Exercise the packaged tzdata fallback used by Windows, even on Unix.
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "PYTHONTZPATH": ""}
        port = _free_port()
        url = f"http://127.0.0.1:{port}/"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            try:
                assert _run([str(tokenhub), "start", "--port", str(port), "--no-open"], env=env, timeout=60).strip() == url
            except AssertionError as error:
                log = home / ".tokenhub" / "runtime.log"
                if log.exists():
                    raise AssertionError(
                        f"{error}\nserver log:\n{log.read_text(errors='replace')[-4000:]}"
                    ) from error
                raise
            with opener.open(url, timeout=3) as response:
                html = response.read().decode("utf-8")
                assert response.status == 200 and "<html" in html.lower()
            asset = re.search(r'(?:src|href)="(/assets/[^\"]+)"', html)
            assert asset is not None, "dashboard has no compiled asset reference"
            with opener.open(url.rstrip("/") + asset.group(1), timeout=3) as response:
                assert response.status == 200 and response.read(), "compiled asset is empty"
            with opener.open(url + "api/v1/status", timeout=3) as response:
                assert json.load(response) == {"status": "ok"}
            query = "api/v1/usage/trends?time_zone=Asia%2FKolkata&from=2026-09-27T18:30:00Z&to=2026-09-28T18:30:00Z&granularity=week"
            with opener.open(url + query, timeout=3) as response:
                trends = json.load(response)
            assert trends["period"]["time_zone"] == "Asia/Kolkata"
            assert trends["current"]["event_count"] == trends["previous"]["event_count"] == 0
            assert len(trends["buckets"]) == 1
            assert trends["comparison"]["workload_tokens"]["percent_change"] is None
            database = home / ".tokenhub" / "tokenhub.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            assert revision == (head_revision(path),), f"unexpected Alembic revision: {revision}"
            assert _run([str(tokenhub), "status"], env=env).strip() == url
            assert _run([str(tokenhub), "start", "--no-open"], env=env).strip() == url
            open_script = (
                "import webbrowser; from tokenhub.cli import main; "
                "webbrowser.open = lambda url: True; raise SystemExit(main(['open']))"
            )
            assert _run([str(python), "-c", open_script], env=env).strip() == url
        finally:
            _run([str(tokenhub), "stop"], env=env, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args()
    for archive in args.archives:
        verify_archive(archive)
        smoke_install(archive)
        print(f"verified {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
