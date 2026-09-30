"""Bounded, path-free job history, independent of import transactions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TERMINAL = {"completed", "completed_with_errors", "failed", "interrupted"}


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CollectionJob:
    kind: str
    argument: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "queued"
    stage: str = "queued"
    provider: str | None = None
    created_at: str = field(default_factory=now)
    started_at: str | None = None
    finished_at: str | None = None
    files_total: int | None = None
    files_completed: int = 0
    bytes_read: int = 0
    bytes_total: int | None = None
    records_read: int = 0
    records_saved: int = 0
    records_total: int | None = None
    skipped_files: int = 0
    inserted_events: int = 0
    duplicate_events: int = 0
    unsupported_records: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    value: Any = field(default=None, repr=False)
    exception: Exception | None = field(default=None, repr=False)
    progress_revision: int = 0
    last_report_at: float = field(default=0, repr=False)

    def snapshot(self) -> dict[str, Any]:
        keys = ("job_id", "kind", "state", "stage", "provider", "created_at", "started_at",
                "finished_at", "files_total", "files_completed", "bytes_read", "bytes_total",
                "records_read", "records_saved", "records_total", "skipped_files", "inserted_events",
                "duplicate_events", "unsupported_records", "error", "result", "progress_revision")
        result = {key: getattr(self, key) for key in keys}
        end = self.finished_at or now()
        result["elapsed_seconds"] = round(max(0, (datetime.fromisoformat(end) -
                                      datetime.fromisoformat(self.started_at or self.created_at)).total_seconds()), 1)
        return result

    def update(self, stage: str | None = None, **values: Any) -> bool:
        stamp = time.monotonic()
        # Counters and stage changes must never be lost to throttling.
        forced = stage != self.stage if stage is not None else False
        forced = forced or any(key not in {"bytes_read", "records_read", "records_saved"} for key in values)
        if stage is not None:
            self.stage = stage
        for key, value in values.items():
            if key.endswith("_delta"):
                target = {"inserted_delta": "inserted_events", "duplicate_delta": "duplicate_events",
                          "unsupported_delta": "unsupported_records"}[key]
                setattr(self, target, getattr(self, target) + value)
            else:
                setattr(self, key, value)
        # Retain the latest counters even when the revision notification is
        # throttled: a long next batch must not leave a stale progress snapshot.
        if not forced and stamp - self.last_report_at < .2:
            return False
        self.last_report_at = stamp
        self.progress_revision += 1
        return True


def load_history(directory: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((directory / "collection-jobs.json").read_text())
        if not isinstance(data, list):
            return []
        history = []
        for item in data[-50:]:
            if not isinstance(item, dict) or not isinstance(item.get("job_id"), str):
                continue
            if item.get("state") not in _TERMINAL:
                item.update(state="interrupted", stage="interrupted", finished_at=now(),
                            error="Collection was interrupted; refresh data to continue.")
            history.append(item)
        return history
    except (OSError, ValueError):
        return []


def save_history(directory: Path, snapshots: list[dict[str, Any]]) -> None:
    # Job acceptance must not wait behind a SQLite import write transaction.
    # Only path-free metadata is written, with an atomic replacement.
    descriptor, filename = tempfile.mkstemp(prefix=".jobs-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(snapshots[-50:], output, separators=(",", ":"))
        os.replace(filename, directory / "collection-jobs.json")
    finally:
        if os.path.exists(filename):
            os.unlink(filename)
