"""Filesystem containment checks for user-approved source roots."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SECURE_DIR_FD_TRAVERSAL = (
    os.open in os.supports_dir_fd
    and bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
)


@dataclass(frozen=True, slots=True)
class AnchoredDirectory:
    """An absolute directory held open across descriptor-relative operations."""

    path: Path
    descriptor: int
    device: int
    inode: int


@contextmanager
def anchor_directory(root: Path) -> Iterator[AnchoredDirectory]:
    """Anchor ``root`` from ``/`` without following any path component."""
    try:
        descriptor = _open_absolute_directory(root)
    except (OSError, ValueError) as error:
        raise ValueError("approved root cannot be opened safely") from error
    try:
        root_stat = os.fstat(descriptor)
        yield AnchoredDirectory(
            path=root,
            descriptor=descriptor,
            device=root_stat.st_dev,
            inode=root_stat.st_ino,
        )
    finally:
        os.close(descriptor)


def list_directory(descriptor: int) -> list[str]:
    """List names through an already anchored directory descriptor."""
    _require_secure_directory_enumeration()
    return os.listdir(descriptor)


def stat_directory_entry(name: str, parent_descriptor: int) -> os.stat_result:
    """Inspect one direct child without following a symbolic link."""
    _require_secure_directory_enumeration()
    _validate_child_name(name)
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def open_directory_entry(name: str, parent_descriptor: int) -> int:
    """Open one direct child directory without following a symbolic link."""
    _validate_child_name(name)
    return _open_directory(name, parent_descriptor)


def directory_identity(root: Path) -> tuple[int, int]:
    """Return a directory identity acquired without following any component."""
    with anchor_directory(root) as anchored:
        return anchored.device, anchored.inode


def validate_source_path(
    candidate: Path,
    root: Path,
    expected_root_identity: tuple[int, int] | None = None,
) -> Path:
    """Validate one regular source beneath the original no-follow root boundary."""
    try:
        descriptor = open_source_path(candidate, root, expected_root_identity)
    except (OSError, ValueError) as error:
        raise ValueError("source path is outside the approved root") from error
    os.close(descriptor)
    # Discovery supplies canonical lexical paths. Never re-resolve them through a
    # replacement symlink and accidentally persist a redefined trust boundary.
    return candidate


def open_source_path(
    candidate: Path,
    root: Path,
    expected_root_identity: tuple[int, int] | None = None,
) -> int:
    """Open a regular source by no-follow traversal from ``/`` through ``root``."""
    _require_secure_directory_traversal()
    relative_path = _lexical_relative_path(candidate, root)
    root_descriptor = _open_absolute_directory(root)
    directory_descriptors = [root_descriptor]
    try:
        if expected_root_identity is not None:
            root_stat = os.fstat(root_descriptor)
            if (root_stat.st_dev, root_stat.st_ino) != expected_root_identity:
                raise ValueError("approved root identity changed")
        for component in relative_path.parts[:-1]:
            directory_descriptors.append(
                _open_directory(component, directory_descriptors[-1])
            )
        return _open_regular_file(relative_path.name, directory_descriptors[-1])
    finally:
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _lexical_relative_path(candidate: Path, root: Path) -> Path:
    if not _is_absolute_clean_path(root) or not _is_absolute_clean_path(candidate):
        raise ValueError("source paths must be clean and absolute")
    try:
        relative_path = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("source is outside its approved root") from error
    if not relative_path.parts:
        raise ValueError("source cannot be its approved root")
    return relative_path


def _require_secure_directory_traversal() -> None:
    if not _SECURE_DIR_FD_TRAVERSAL:
        raise ValueError("secure directory traversal is unavailable")


def _require_secure_directory_enumeration() -> None:
    _require_secure_directory_traversal()
    if (
        os.listdir not in os.supports_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise ValueError("secure directory enumeration is unavailable")


def _open_absolute_directory(root: Path) -> int:
    _require_secure_directory_traversal()
    if not _is_absolute_clean_path(root):
        raise ValueError("approved root must be clean and absolute")
    descriptor = _open_directory(Path("/"))
    try:
        for component in root.parts[1:]:
            child_descriptor = _open_directory(component, descriptor)
            os.close(descriptor)
            descriptor = child_descriptor
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _is_absolute_clean_path(path: Path) -> bool:
    return path.is_absolute() and all(part not in {".", ".."} for part in path.parts)


def _open_directory(path: Path | str, parent_descriptor: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path, flags, dir_fd=parent_descriptor)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("source is not a directory")
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor


def _validate_child_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("directory child must be a single path component")


def _open_regular_file(name: str, parent_descriptor: int) -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("source is not a regular file")
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor
