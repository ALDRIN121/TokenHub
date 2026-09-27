"""Validate only identifiers used for usage attribution, never message content."""

from typing import Any


def usage_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        return None
    if any(character.isspace() or ord(character) < 32 for character in value):
        return None
    return None if value == "<synthetic>" else value
