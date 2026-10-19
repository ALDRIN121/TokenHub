"""Release archives must contain every file needed after pip install."""

from io import BytesIO
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as open_tar
from zipfile import ZipFile

import pytest

from scripts.verify_distribution import head_revision, verify_archive


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


def test_archive_requires_packaged_migrations(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.4-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        for name in ("tokenhub/web/index.html", "tokenhub/web/assets/app.js",
                     "tokenhub/web/assets/app.css",
                     "tokenhub-0.1.4.dist-info/entry_points.txt", "tokenhub-0.1.4.dist-info/LICENSE"):
            archive.writestr(name, "test")
    with pytest.raises(AssertionError, match="missing packaged database migrations"):
        verify_archive(wheel)


def test_head_revision_is_derived_from_the_packaged_graph(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.8-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "tokenhub/database/migrations/versions/0007_multiple_usage_roots.py",
            'revision: str = "0007_multiple_usage_roots"\n'
            'down_revision: str | None = "0006_performance_jobs"\n',
        )
        archive.writestr(
            "tokenhub/database/migrations/versions/0008_canonical_usage.py",
            'revision: str = "0008_canonical_usage"\n'
            'down_revision: str | None = "0007_multiple_usage_roots"\n',
        )
    assert head_revision(wheel) == "0008_canonical_usage"


def test_head_revision_rejects_a_branched_history(tmp_path: Path) -> None:
    wheel = tmp_path / "tokenhub-0.1.8-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "tokenhub/database/migrations/versions/0008a.py",
            'revision: str = "0008a"\ndown_revision: str | None = "0007_multiple_usage_roots"\n',
        )
        archive.writestr(
            "tokenhub/database/migrations/versions/0008b.py",
            'revision: str = "0008b"\ndown_revision: str | None = "0007_multiple_usage_roots"\n',
        )
    with pytest.raises(AssertionError, match="expected one packaged Alembic head"):
        head_revision(wheel)
