"""The launcher owns one verified background child and cleans up failures."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psutil
import pytest
from tokenhub.runtime.instance import InstanceRecord, read_record, write_record
from tokenhub.runtime.manager import RuntimeManager


class Child:
    pid = 4567

    def __init__(self, *, exited=False):
        self.exited = exited
        self.terminated = False
        self.waited = False

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.terminated = True
        self.exited = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_existing_verified_instance_is_reused_on_its_recorded_port(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 9000, "a" * 64)
    write_record(tmp_path, record)
    monkeypatch.setattr(module, "probe", lambda _: True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    manager = RuntimeManager(tmp_path)
    assert manager.start() == "http://127.0.0.1:9000/"
    assert manager.status() == "http://127.0.0.1:9000/"
    with pytest.raises(RuntimeError, match="already running"):
        manager.start(port=7432)


def test_two_simultaneous_starts_spawn_one_child(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    calls = []
    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: calls.append((a, k)) or child)
    monkeypatch.setattr(module, "probe", lambda _: bool(calls))
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        return RuntimeManager(tmp_path).start()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert results == ["http://127.0.0.1:7432/"] * 2
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][1:] == ["-m", "tokenhub", "_serve", "7432"]
    assert len(kwargs["env"]["TOKENHUB_INTERNAL_CONTROL_TOKEN"]) == 64
    assert kwargs["stdin"] is module.subprocess.DEVNULL
    assert kwargs["stdout"] is kwargs["stderr"] or kwargs["stderr"] is module.subprocess.STDOUT
    assert kwargs["start_new_session"] == (os.name != "nt")
    assert read_record(tmp_path).pid == child.pid


def test_overlapping_slow_starts_wait_for_one_verified_child(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    calls = []
    child = Child()
    ready_at = float("inf")

    def spawn(*args, **kwargs):
        nonlocal ready_at
        calls.append(child)
        ready_at = time.monotonic() + 10.5
        return child

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "probe", lambda _: time.monotonic() >= ready_at)
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    barrier = threading.Barrier(2)

    def start():
        barrier.wait()
        return RuntimeManager(tmp_path).start()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert results == ["http://127.0.0.1:7432/"] * 2
    assert calls == [child]
    assert read_record(tmp_path).pid == child.pid


def test_slow_startup_becomes_ready_after_ten_seconds(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child()
    elapsed = 0.0

    def advance_time(interval):
        nonlocal elapsed
        elapsed += interval

    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "probe", lambda _: elapsed >= 15.0)
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance_time)
    )

    assert RuntimeManager(tmp_path).start() == "http://127.0.0.1:7432/"
    assert not child.terminated and not child.waited
    assert read_record(tmp_path).pid == child.pid


def test_foreign_listener_never_becomes_ready_and_new_child_is_cleaned(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "probe", lambda _: False)
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    clock = iter([0, 31])
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=time.sleep)
    )
    with pytest.raises(RuntimeError, match="runtime.log"):
        RuntimeManager(tmp_path).start()
    assert child.terminated and child.waited
    assert read_record(tmp_path) is None


def test_early_exit_cleans_record_and_reports_log(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child(exited=True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "probe", lambda _: False)
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    with pytest.raises(RuntimeError, match="runtime.log"):
        RuntimeManager(tmp_path).start()
    assert child.waited
    assert read_record(tmp_path) is None


def test_stop_refuses_unverified_record_without_contacting_pid(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 7432, "a" * 64)
    write_record(tmp_path, record)
    monkeypatch.setattr(module, "probe", lambda _: False)
    monkeypatch.setattr(module, "request_stop", lambda _: pytest.fail("contacted foreign process"))
    assert RuntimeManager(tmp_path).stop() is False
    assert read_record(tmp_path) == record


def test_stop_waits_for_shutdown_then_clears_matching_record(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 7432, "a" * 64)
    write_record(tmp_path, record)
    probes = iter([True, True, False])
    monkeypatch.setattr(module, "probe", lambda _: next(probes))
    monkeypatch.setattr(module, "request_stop", lambda _: True)
    monkeypatch.setattr(module, "_recorded_process_alive", lambda _: False)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    assert RuntimeManager(tmp_path).stop() is True
    assert read_record(tmp_path) is None
    assert RuntimeManager(tmp_path).stop() is False


def test_failed_stop_retains_record(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 7432, "a" * 64)
    write_record(tmp_path, record)
    monkeypatch.setattr(module, "probe", lambda _: True)
    monkeypatch.setattr(module, "request_stop", lambda _: False)
    with pytest.raises(RuntimeError, match="could not be stopped"):
        RuntimeManager(tmp_path).stop()
    assert read_record(tmp_path) == record


def test_unresponsive_live_instance_keeps_its_record_and_blocks_new_child(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 9000, "a" * 64)
    write_record(tmp_path, record)
    monkeypatch.setattr(module, "probe", lambda _: False)
    monkeypatch.setattr(module, "_recorded_process_alive", lambda _: True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(RuntimeError, match="may still be running"):
        RuntimeManager(tmp_path).start(port=7432)
    assert read_record(tmp_path) == record


def test_unresponsive_real_process_cannot_be_replaced(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        record = InstanceRecord(child.pid, 9000, "a" * 64, psutil.Process(child.pid).create_time())
        write_record(tmp_path, record)
        monkeypatch.setattr(module, "probe", lambda _: False)
        monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
        with pytest.raises(RuntimeError, match="may still be running"):
            RuntimeManager(tmp_path).start(port=7432)
        assert child.poll() is None
        assert read_record(tmp_path) == record
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_record_write_failure_terminates_only_new_child(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "_process_start_time", lambda _: 123.0)
    monkeypatch.setattr(module, "write_record", lambda *a: (_ for _ in ()).throw(OSError("full disk")))
    with pytest.raises(RuntimeError, match="runtime.log"):
        RuntimeManager(tmp_path).start()
    assert child.terminated and child.waited
    assert read_record(tmp_path) is None


def test_reused_pid_is_not_the_recorded_process(monkeypatch):
    from tokenhub.runtime import manager as module

    class ReusedProcess:
        def create_time(self):
            return 200.0

        def is_running(self):
            return True

        def status(self):
            return "running"

    monkeypatch.setattr(module.psutil, "Process", lambda _: ReusedProcess())
    assert not module._recorded_process_alive(InstanceRecord(123, 7432, "a" * 64, 100.0))


def test_stop_waits_for_real_process_to_exit_after_http_shutdown(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; sys.stdin.buffer.read(1); time.sleep(0.3)"],
        stdin=subprocess.PIPE,
    )
    stopping = False
    try:
        record = InstanceRecord(child.pid, 7432, "a" * 64, psutil.Process(child.pid).create_time())
        write_record(tmp_path, record)
        monkeypatch.setattr(module, "probe", lambda _: not stopping)

        def request_stop(_):
            nonlocal stopping
            assert child.stdin is not None
            child.stdin.write(b"x")
            child.stdin.flush()
            stopping = True
            return True

        monkeypatch.setattr(module, "request_stop", request_stop)
        assert RuntimeManager(tmp_path).stop() is True
        assert child.poll() is not None, "stop returned before the server released its resources"
        assert read_record(tmp_path) is None
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        if child.stdin is not None:
            child.stdin.close()


def test_stop_retains_record_when_process_outlives_http_shutdown(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    record = InstanceRecord(123, 7432, "a" * 64, 100.0)
    write_record(tmp_path, record)
    probes = iter([True, False])
    clock = iter([0, 11])
    monkeypatch.setattr(module, "probe", lambda _: next(probes))
    monkeypatch.setattr(module, "request_stop", lambda _: True)
    monkeypatch.setattr(module, "_recorded_process_alive", lambda _: True)
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=time.sleep)
    )
    with pytest.raises(RuntimeError, match="did not stop in time"):
        RuntimeManager(tmp_path).stop()
    assert read_record(tmp_path) == record
