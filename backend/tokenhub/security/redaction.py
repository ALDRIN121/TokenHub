"""Central redaction for credentials that may appear in diagnostics."""

import re

_BEARER_VALUE = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_REDACTED = "[REDACTED]"


def redact_sensitive(value: str) -> str:
    """Replace recognized credential values while preserving diagnostic context."""
    value = _BEARER_VALUE.sub(rf"\1{_REDACTED}", value)
    return _OPENAI_STYLE_KEY.sub(_REDACTED, value)
