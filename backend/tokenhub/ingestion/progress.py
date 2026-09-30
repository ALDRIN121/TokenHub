"""Cooperative progress for parsers without retaining their input records."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, BinaryIO, cast


class ScanInterrupted(RuntimeError):
    """The owner stopped before an incomplete source could be committed."""


_reporter: ContextVar[Callable[..., None] | None] = ContextVar("scan_progress", default=None)


def report(stage: str | None = None, **values: Any) -> None:
    callback = _reporter.get()
    if callback is not None:
        callback(stage=stage, **values)


@contextmanager
def reporting(callback: Callable[..., None]) -> Iterator[None]:
    token = _reporter.set(callback)
    try:
        yield
    finally:
        _reporter.reset(token)


class _ProgressReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.records_read = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)

    def read(self, size: int = -1) -> bytes:
        value = self.stream.read(size)
        report("reading", bytes_read=self.stream.tell())
        return value

    def readline(self, size: int = -1) -> bytes:
        value = self.stream.readline(size)
        self.records_read += bool(value)
        report("reading", bytes_read=self.stream.tell(), records_read=self.records_read)
        return value


@contextmanager
def progress_stream(stream: BinaryIO) -> Iterator[BinaryIO]:
    import os
    with stream:
        report("reading", bytes_read=0, bytes_total=os.fstat(stream.fileno()).st_size,
               records_read=0, records_saved=0, records_total=None)
        yield cast(BinaryIO, _ProgressReader(stream))
