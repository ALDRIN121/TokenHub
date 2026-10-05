"""Release archives must contain every file needed after pip install."""

from io import BytesIO
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as open_tar
from zipfile import ZipFile

import pytest

from scripts.verify_distribution import verify_archive


def test_archive_requires_bundled_index(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("tokenhub/__init__.py", "")
    with pytest.raises(AssertionError, match="tokenhub/web/index.html"):
        verify_archive(wheel)


def test_source_archive_normalizes_project_and_backend_prefixes(tmp_path: Path) -> None:
    source = tmp_path / "tokenhub-0.1.0.tar.gz"
    names = (
        "backend/tokenhub/web/index.html",
        "backend/tokenhub/web/assets/app.js",
        "backend/tokenhub/web/assets/app.css",
        "backend/tokenhub/database/migrations/versions/0006_performance_jobs.py",
        "backend/tokenhub/database/migrations/versions/0007_multiple_usage_roots.py",
        "backend/tokenhub.egg-info/entry_points.txt",
        "LICENSE",
    )
    with open_tar(source, "w:gz") as archive:
        for name in names:
            payload = b"test"
            info = TarInfo(f"tokenhub-0.1.0/{name}")
            info.size = len(payload)
            archive.addfile(info, BytesIO(payload))
    verify_archive(source)


def test_archive_requires_current_consent_migration(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.4-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        for name in ("tokenhub/web/index.html", "tokenhub/web/assets/app.js",
                     "tokenhub/web/assets/app.css", "tokenhub/database/migrations/versions/0006_performance_jobs.py",
                     "tokenhub-0.1.4.dist-info/entry_points.txt", "tokenhub-0.1.4.dist-info/LICENSE"):
            archive.writestr(name, "test")
    with pytest.raises(AssertionError, match="0007_multiple_usage_roots"):
        verify_archive(wheel)
