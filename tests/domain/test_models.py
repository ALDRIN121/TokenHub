from datetime import UTC, datetime

from tokenhub.domain.models import (
    Confidence,
    MeasurementType,
    Provider,
    Quality,
    UsageEvent,
    confidence_from_evidence,
)


def test_workload_excludes_cache_and_reasoning() -> None:
    """Fails if cache or reasoning tokens are counted as workload."""
    event = UsageEvent(
        connector_id="codex-local",
        provider=Provider.CODEX,
        source_id="source-a",
        record_identity="7",
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=100,
        cache_read_tokens=70,
        cache_write_tokens=10,
        output_total_tokens=25,
        reasoning_tokens=20,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.EXACT,
        parser_version="codex-jsonl-v1",
    )

    assert event.workload_tokens == 125


def test_workload_is_unknown_when_a_total_is_missing() -> None:
    """Fails if an absent token total is invented as zero."""
    event = UsageEvent(
        connector_id="codex-local",
        provider=Provider.CODEX,
        source_id="source-a",
        record_identity="8",
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=None,
        output_total_tokens=25,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.PARTIAL,
        parser_version="codex-jsonl-v1",
    )

    assert event.workload_tokens is None


def test_confidence_is_high_when_several_signals_agree() -> None:
    """Fails if two independent discovery signals are not enough for high."""
    assert confidence_from_evidence(("known_root_exists", "configuration_found")) is Confidence.HIGH
    assert (
        confidence_from_evidence(
            ("executable_on_path", "known_root_exists", "session_source_found")
        )
        is Confidence.HIGH
    )


def test_confidence_is_medium_for_exactly_one_signal() -> None:
    """Fails if a single signal is over-reported as high."""
    assert confidence_from_evidence(("known_root_exists",)) is Confidence.MEDIUM


def test_confidence_is_low_when_nothing_was_found() -> None:
    """Fails if absent evidence is reported as a positive signal."""
    assert confidence_from_evidence(()) is Confidence.LOW


def test_unknown_codes_do_not_inflate_confidence() -> None:
    """A future evidence code must not raise confidence until it is taught to."""
    assert confidence_from_evidence(("future_signal", "another_new_signal")) is Confidence.LOW
    assert confidence_from_evidence(("future_signal", "known_root_exists")) is Confidence.MEDIUM


def test_repeated_codes_count_once() -> None:
    """Fails if one signal repeated twice is treated as two independent signals."""
    assert confidence_from_evidence(("known_root_exists", "known_root_exists")) is Confidence.MEDIUM
