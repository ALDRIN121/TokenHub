"""Compatibility shims behave the same on every supported interpreter."""

from datetime import datetime, timezone

from tokenhub.compat import UTC, StrEnum, parse_iso_timestamp


def test_utc_matches_the_standard_library_alias() -> None:
    assert UTC is timezone.utc


def test_parse_iso_timestamp_accepts_z_and_long_fractions() -> None:
    """Fails if connector timestamps only parse on Python 3.11 and newer."""
    assert parse_iso_timestamp("2026-11-01T05:30Z") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    assert parse_iso_timestamp("2026-09-20T10:00:00.123456789Z") == datetime(
        2026, 9, 20, 10, 0, 0, 123456, tzinfo=UTC
    )


def test_str_enum_converts_and_formats_as_its_value() -> None:
    class Colour(StrEnum):
        BLUE = "blue"

    assert str(Colour.BLUE) == "blue"
    assert f"{Colour.BLUE}" == "blue"
    assert Colour.BLUE == "blue"
