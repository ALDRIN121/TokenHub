"""The launcher owns one verified background child and cleans up failures."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

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


def test_foreign_listener_never_becomes_ready_and_new_child_is_cleaned(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "probe", lambda _: False)
    clock = iter([0, 11])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="runtime.log"):
        RuntimeManager(tmp_path).start()
    assert child.terminated and child.waited
    assert read_record(tmp_path) is None


def test_early_exit_cleans_record_and_reports_log(tmp_path, monkeypatch):
    from tokenhub.runtime import manager as module

    child = Child(exited=True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module, "probe", lambda _: False)
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
