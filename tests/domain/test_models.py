from datetime import UTC, datetime

from tokenhub.domain.models import MeasurementType, Provider, Quality, UsageEvent


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
