"""Compatibility shims for running TokenHub on Python 3.10 and newer."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

#: ``datetime.UTC`` only exists from Python 3.11; it is an alias of
#: ``timezone.utc``, so the same object works on every supported version.
UTC = timezone.utc

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - exercised by the Python 3.10 test job
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10 stand-in for :class:`enum.StrEnum`.

        String formatting and conversion return the member value, matching the
        standard library implementation added in Python 3.11.
        """

        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, format_spec: str) -> str:
            return format(self.value, format_spec)


#: Python 3.10 accepts at most six fractional-second digits.
_EXTRA_FRACTIONAL_DIGITS = re.compile(r"(T\d{2}:\d{2}:\d{2}\.\d{6})\d+")


def parse_iso_timestamp(value: str) -> datetime:
    """Parse ISO-8601 text, accepting a ``Z`` suffix on Python 3.10.

    ``datetime.fromisoformat`` only learned to accept ``Z`` and fractional
    seconds longer than six digits in Python 3.11, while connector files
    routinely contain both.
    """
    if sys.version_info >= (3, 11):
        return datetime.fromisoformat(value)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(_EXTRA_FRACTIONAL_DIGITS.sub(r"\1", text))
