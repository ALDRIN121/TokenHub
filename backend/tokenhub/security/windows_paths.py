"""No-follow Windows file handles for approved local usage sources.

Windows has no ``openat``. Each component is opened without following a
reparse point, and the resulting handle's final path is checked against its
parent handle. Only the final regular-file handle is used to read source data.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import stat
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from typing import Any

_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_OPEN_REPARSE_POINT = 0x00200000
_BACKUP_SEMANTICS = 0x02000000
_REPARSE_ATTRIBUTE = 0x00000400
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def sqlite_file_id(value: int) -> int:
    """Map a Windows unsigned 64-bit file ID bijectively into SQLite's signed range."""
    if not 0 <= value < 2**64:
        raise ValueError("filesystem identity exceeds 64 bits")
    return value if value < 2**63 else value - 2**64


def storage_identity(metadata: os.stat_result) -> tuple[int, int]:
    return sqlite_file_id(metadata.st_dev), sqlite_file_id(metadata.st_ino)


def is_beneath(child: str, parent: str) -> bool:
    """Compare Windows path components, including case and drive semantics."""
    normalized_child = ntpath.normcase(ntpath.normpath(child))
    normalized_parent = ntpath.normcase(ntpath.normpath(parent))
    try:
        return (
            normalized_child != normalized_parent
            and ntpath.commonpath((normalized_child, normalized_parent)) == normalized_parent
        )
    except ValueError:
        return False


@lru_cache(maxsize=1)
def _kernel32() -> Any:
    if os.name != "nt":
        raise OSError("Windows file handles are unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _open_handle(path: Path, *, directory: bool | None) -> int:
    import msvcrt

    kernel32 = _kernel32()
    handle = kernel32.CreateFileW(
        str(path),
        _GENERIC_READ,
        _SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _OPEN_REPARSE_POINT | _BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), f"cannot open {path}")  # type: ignore[attr-defined]
    try:
        descriptor = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
            handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    try:
        metadata = os.fstat(descriptor)
        if getattr(metadata, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE:
            raise ValueError("reparse points are outside the approved root")
        if directory is True and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("source is not a directory")
        if directory is False and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("source is not a regular file")
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _path_from_handle(descriptor: int) -> Path:
    import msvcrt

    buffer = ctypes.create_unicode_buffer(32768)
    length = _kernel32().GetFinalPathNameByHandleW(
        msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0  # type: ignore[attr-defined]
    )
    if length == 0 or length >= len(buffer):
        raise OSError("cannot resolve Windows file handle")
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[len("\\\\?\\UNC\\"):]
    elif path.startswith("\\\\?\\"):
        path = path[len("\\\\?\\"):]
    return Path(path)


def open_directory_entry(name: str, parent_descriptor: int) -> int:
    parent = _path_from_handle(parent_descriptor)
    _assert_parent_identity(parent, parent_descriptor)
    descriptor = _open_handle(parent / name, directory=True)
    try:
        _assert_parent_identity(parent, parent_descriptor)
        if not is_beneath(str(_path_from_handle(descriptor)), str(parent)):
            raise ValueError("directory escaped its approved parent")
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def open_absolute_directory(root: Path) -> int:
    if not root.is_absolute() or any(part in {".", ".."} for part in root.parts):
        raise ValueError("approved root must be clean and absolute")
    descriptor = _open_handle(Path(root.anchor), directory=True)
    try:
        for component in root.parts[1:]:
            child = open_directory_entry(component, descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def list_directory(descriptor: int) -> list[str]:
    """Enumerate a handle's path, verifying its identity around the listing."""
    path = _path_from_handle(descriptor)
    identity = _identity(descriptor)
    if _identity_at(path) != identity:
        raise ValueError("approved directory changed")
    names = os.listdir(path)
    if _identity_at(path) != identity:
        raise ValueError("approved directory changed")
    return names


def stat_directory_entry(name: str, parent_descriptor: int) -> os.stat_result:
    parent = _path_from_handle(parent_descriptor)
    _assert_parent_identity(parent, parent_descriptor)
    # Metadata inspection must include reparse points so discovery can skip
    # links without treating the whole provider as broken.
    metadata = os.stat(parent / name, follow_symlinks=False)
    _assert_parent_identity(parent, parent_descriptor)
    return metadata


def open_source_path(
    root: Path, relative_path: Path, expected_root_identity: tuple[int, int] | None
) -> int:
    root_descriptor = open_absolute_directory(root)
    directories = [root_descriptor]
    try:
        if expected_root_identity is not None and _identity(root_descriptor) != expected_root_identity:
            raise ValueError("approved root identity changed")
        for component in relative_path.parts[:-1]:
            directories.append(open_directory_entry(component, directories[-1]))
        parent = _path_from_handle(directories[-1])
        _assert_parent_identity(parent, directories[-1])
        descriptor = _open_handle(parent / relative_path.name, directory=False)
        try:
            _assert_parent_identity(parent, directories[-1])
            if not is_beneath(str(_path_from_handle(descriptor)), str(parent)):
                raise ValueError("source escaped its approved root")
            return descriptor
        except (OSError, ValueError):
            os.close(descriptor)
            raise
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


def _identity(descriptor: int) -> tuple[int, int]:
    return storage_identity(os.fstat(descriptor))


def _identity_at(path: Path) -> tuple[int, int]:
    descriptor = _open_handle(path, directory=True)
    try:
        return _identity(descriptor)
    finally:
        os.close(descriptor)


def _assert_parent_identity(path: Path, descriptor: int) -> None:
    if _identity_at(path) != _identity(descriptor):
        raise ValueError("approved directory changed")
