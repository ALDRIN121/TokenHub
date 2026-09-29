"""Launch and identify one detached TokenHub server per data directory."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psutil
from filelock import FileLock

from tokenhub.runtime.client import probe, request_stop
from tokenhub.runtime.instance import (
    InstanceRecord,
    delete_record,
    read_record,
    write_record,
)
from tokenhub.settings import DEFAULT_PORT, TokenHubSettings

_READY_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.1


def _url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _process_start_time(pid: int) -> float:
    return psutil.Process(pid).create_time()


def _recorded_process_alive(record: InstanceRecord) -> bool:
    """Treat uncertain ownership as live so a failed probe cannot orphan it."""
    try:
        process = psutil.Process(record.pid)
        if record.started_at is not None and abs(process.create_time() - record.started_at) > 0.001:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


class RuntimeManager:
    """Serialize launch decisions and trust only token-proven instances."""

    def __init__(self, data_directory: Path) -> None:
        self.data_directory = Path(data_directory)

    def _prepare_directory(self) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.data_directory, 0o700)

    def status(self) -> str | None:
        record = read_record(self.data_directory)
        return _url(record.port) if record is not None and probe(record) else None

    def stop(self) -> bool:
        """Stop only the server proven by the private token, never a recorded PID."""
        self._prepare_directory()
        with FileLock(str(self.data_directory / "runtime.lock"), timeout=10):
            record = read_record(self.data_directory)
            if record is None or not probe(record):
                return False
            if not request_stop(record):
                raise RuntimeError("Token Hub could not be stopped")
            deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
            while probe(record):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Token Hub did not stop in time")
                time.sleep(_POLL_INTERVAL_SECONDS)
            if read_record(self.data_directory) == record:
                delete_record(self.data_directory)
            return True

    def start(self, port: int | None = None) -> str:
        if port is not None:
            TokenHubSettings(port=port)
        self._prepare_directory()
        with FileLock(str(self.data_directory / "runtime.lock"), timeout=10):
            existing = read_record(self.data_directory)
            if existing is not None:
                if probe(existing):
                    if port is not None and port != existing.port:
                        raise RuntimeError("Token Hub is already running on another port")
                    return _url(existing.port)
                if _recorded_process_alive(existing):
                    raise RuntimeError(
                        "Token Hub may still be running but is not responding; "
                        f"check {self.data_directory / 'runtime.log'}"
                    )

            selected_port = DEFAULT_PORT if port is None else port
            token = secrets.token_hex(32)
            log_path = self.data_directory / "runtime.log"
            try:
                with os.fdopen(
                    os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600),
                    "ab",
                    buffering=0,
                ) as log_file:
                    if os.name == "posix":
                        os.fchmod(log_file.fileno(), 0o600)
                    child = subprocess.Popen(
                        [sys.executable, "-m", "tokenhub", "_serve", str(selected_port)],
                        env={**os.environ, "TOKENHUB_INTERNAL_CONTROL_TOKEN": token},
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        start_new_session=os.name != "nt",
                        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0,
                    )
            except OSError as error:
                raise RuntimeError(f"Could not start Token Hub; see {log_path}") from error

            record: InstanceRecord | None = None
            ready = False
            try:
                record = InstanceRecord(child.pid, selected_port, token, _process_start_time(child.pid))
                write_record(self.data_directory, record)
                deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
                while True:
                    if child.poll() is not None:
                        break
                    if probe(record):
                        ready = True
                        return _url(selected_port)
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_POLL_INTERVAL_SECONDS)
                raise RuntimeError("server did not become ready")
            except Exception as error:
                raise RuntimeError(f"Token Hub did not start; see {log_path}") from error
            finally:
                if not ready:
                    self._cleanup_failed_child(child, record)

    def _cleanup_failed_child(
        self, child: subprocess.Popen[bytes], record: InstanceRecord | None
    ) -> None:
        try:
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        finally:
            if record is not None and read_record(self.data_directory) == record:
                delete_record(self.data_directory)
