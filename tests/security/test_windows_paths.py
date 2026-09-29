"""Windows path traversal keeps approved roots and their contents separate."""

import os
import stat
from pathlib import Path

import pytest
from tokenhub.security.windows_paths import is_beneath


def test_windows_path_containment_uses_components_and_casefolding():
    assert is_beneath(r"C:\Users\me\approved\file.json", r"c:\users\ME\approved")
    assert not is_beneath(r"C:\Users\me\approved-other\file.json", r"C:\Users\me\approved")
    assert not is_beneath(r"D:\Users\me\approved\file.json", r"C:\Users\me\approved")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle APIs")
def test_windows_approved_file_and_directory_enumeration(tmp_path: Path):
    from tokenhub.security.paths import (
        anchor_directory,
        list_directory,
        open_source_path,
    )

    root = tmp_path / "approved"
    root.mkdir()
    (root / "session.json").write_text("{}")
    with anchor_directory(root) as anchored:
        assert "session.json" in list_directory(anchored.descriptor)
        descriptor = open_source_path(root / "session.json", root, (anchored.device, anchored.inode))
        try:
            assert os.read(descriptor, 2) == b"{}"
        finally:
            os.close(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle APIs")
def test_windows_reparse_source_is_rejected(tmp_path: Path):
    from tokenhub.security.paths import (
        anchor_directory,
        open_source_path,
        stat_directory_entry,
    )

    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("secret")
    link = root / "session.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with anchor_directory(root) as anchored:
        metadata = stat_directory_entry("session.json", anchored.descriptor)
        assert stat.S_ISLNK(metadata.st_mode)
    with pytest.raises((OSError, ValueError)):
        open_source_path(link, root)
