from pathlib import Path

import pytest
from tokenhub.security.paths import validate_source_path


def test_source_path_rejects_symlink_escape(tmp_path: Path) -> None:
    """Fails if containment is checked before resolving symlinks."""
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    escaped = approved_root / "escaped.jsonl"
    escaped.symlink_to(outside)

    with pytest.raises(ValueError, match="approved root"):
        validate_source_path(escaped, approved_root)


def test_source_path_returns_resolved_path_inside_root(tmp_path: Path) -> None:
    """Fails if an existing approved path is not validated and canonicalized."""
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    source = approved_root / "usage.jsonl"
    source.write_text("{}\n")

    assert validate_source_path(source, approved_root) == source.resolve()
