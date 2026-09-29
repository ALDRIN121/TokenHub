"""The instance record is private, validated, and safely replaced."""

import json
import os
import stat

from tokenhub.runtime.instance import InstanceRecord, read_record, write_record


def test_record_round_trip_and_private_mode(tmp_path):
    record = InstanceRecord(pid=123, port=7432, token="a" * 64)
    write_record(tmp_path, record)
    assert read_record(tmp_path) == record
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "runtime.json").stat().st_mode) == 0o600


def test_record_invalid_data_is_ignored(tmp_path):
    path = tmp_path / "runtime.json"
    assert read_record(tmp_path) is None
    for content in (
        "{",
        "[]",
        json.dumps({"pid": -1, "port": 7432, "token": "a" * 64}),
        json.dumps({"pid": 123, "port": 0, "token": "a" * 64}),
        json.dumps({"pid": 123, "port": 65536, "token": "a" * 64}),
        json.dumps({"pid": 123, "port": 7432, "token": "bad"}),
        json.dumps({"pid": 123, "port": 7432, "token": "z" * 64}),
        json.dumps({"pid": True, "port": 7432, "token": "a" * 64}),
        json.dumps({"pid": 123, "port": 7432, "token": "a" * 64, "started_at": "bad"}),
    ):
        path.write_text(content)
        assert read_record(tmp_path) is None


def test_write_atomically_replaces_existing_record(tmp_path, monkeypatch):
    from tokenhub.runtime import instance

    old = InstanceRecord(pid=123, port=7432, token="a" * 64)
    new = InstanceRecord(pid=456, port=9000, token="b" * 64)
    write_record(tmp_path, old)
    real_replace = instance.os.replace

    def checked_replace(source, destination):
        assert read_record(tmp_path) == old
        assert source.parent == destination.parent == tmp_path
        real_replace(source, destination)

    monkeypatch.setattr(instance.os, "replace", checked_replace)
    write_record(tmp_path, new)
    assert read_record(tmp_path) == new
