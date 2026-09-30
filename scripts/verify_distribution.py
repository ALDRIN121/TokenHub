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

_MIGRATION = "tokenhub/database/migrations/versions/0006_performance_jobs.py"


def verify_archive(path: Path) -> None:
    """Reject an archive missing any runtime or distribution resource."""
    if path.suffix == ".whl":
        with ZipFile(path) as archive:
            members = archive.namelist()
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [
                name.split("/", 1)[1]
                for name in archive.getnames()
                if "/" in name
            ]
        members = [name.removeprefix("backend/") for name in members]
    else:
        raise ValueError(f"unsupported package archive: {path}")

    assert "tokenhub/web/index.html" in members, "missing tokenhub/web/index.html"
    assert any(
        name.startswith("tokenhub/web/assets/") and name.endswith(".js") for name in members
    ), "missing compiled JavaScript"
    assert any(
        name.startswith("tokenhub/web/assets/") and name.endswith(".css") for name in members
    ), "missing compiled CSS"
    assert _MIGRATION in members, f"missing {_MIGRATION}"
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
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        port = _free_port()
        url = f"http://127.0.0.1:{port}/"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            try:
                assert _run([str(tokenhub), "start", "--port", str(port), "--no-open"], env=env, timeout=30).strip() == url
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
            database = home / ".tokenhub" / "tokenhub.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            assert revision == ("0006_performance_jobs",), f"unexpected Alembic revision: {revision}"
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
