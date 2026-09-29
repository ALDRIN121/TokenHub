"""Private, validated instance record in the user's TokenHub data directory."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_RECORD_NAME = "runtime.json"


@dataclass(frozen=True, slots=True)
class InstanceRecord:
    pid: int
    port: int
    token: str


def _valid(record: InstanceRecord) -> bool:
    return (
        type(record.pid) is int
        and record.pid > 0
        and type(record.port) is int
        and 1 <= record.port <= 65535
        and isinstance(record.token, str)
        and _TOKEN_PATTERN.fullmatch(record.token) is not None
    )


def read_record(data_directory: Path) -> InstanceRecord | None:
    """Return only a structurally valid record; never trust it as identity alone."""
    try:
        content = json.loads((data_directory / _RECORD_NAME).read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            return None
        record = InstanceRecord(**content)
    except (OSError, ValueError, TypeError):
        return None
    return record if _valid(record) else None


def write_record(data_directory: Path, record: InstanceRecord) -> None:
    """Replace the record atomically, restricting access before publication."""
    if not _valid(record):
        raise ValueError("invalid TokenHub instance record")
    data_directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=data_directory, prefix=".runtime-", delete=False
        ) as output:
            temporary = Path(output.name)
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            json.dump(asdict(record), output)
            output.write("\n")
        os.replace(temporary, data_directory / _RECORD_NAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delete_record(data_directory: Path) -> None:
    """Remove a stale record; callers serialize lifecycle decisions with a lock."""
    (data_directory / _RECORD_NAME).unlink(missing_ok=True)
