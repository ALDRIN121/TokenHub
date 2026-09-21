from dataclasses import replace
from datetime import UTC, datetime

from tokenhub.domain.models import (
    MeasurementType,
    Provider,
    Quality,
    SyncCursor,
    UsageEvent,
)

from tests.service_support import Services, discover_and_approve_codex, token_record


def test_empty_dashboard_has_unknown_metrics(app_services: Services) -> None:
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens is None
    assert summary.input_total_tokens is None
    assert summary.output_total_tokens is None
    assert summary.cache_read_tokens is None
    assert summary.cache_write_tokens is None
    assert summary.reasoning_tokens is None
    assert summary.event_count == 0
    assert summary.source_freshness == ()


def test_dashboard_keeps_breakdowns_separate_and_reports_quality(
    app_services: Services,
) -> None:
    source_id = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source_id)
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens == 125
    assert summary.input_total_tokens == 100
    assert summary.output_total_tokens == 25
    assert summary.cache_read_tokens == 70
    assert summary.cache_write_tokens == 10
    assert summary.reasoning_tokens == 20
    assert summary.quality_counts[Quality.EXACT] == 1
    freshness = next(s for s in summary.source_freshness if s.source_id == source_id)
    assert freshness.latest_event_at == datetime(2026, 9, 20, 10, tzinfo=UTC)
    assert freshness.source_mtime_ns == app_services.session_file.stat().st_mtime_ns
    assert freshness.parser_version == "codex-jsonl-v1"


def test_missing_values_stay_unknown_but_observed_zero_is_zero(
    app_services: Services,
) -> None:
    app_services.session_file.write_bytes(token_record(1, output_tokens=0))
    source_id = discover_and_approve_codex(app_services)
    app_services.ingestion.rescan(source_id)
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens is None
    assert summary.input_total_tokens is None
    assert summary.output_total_tokens == 0
    assert summary.cache_read_tokens is None
    assert summary.cache_write_tokens is None
    assert summary.reasoning_tokens is None


def test_provider_neutral_sums_only_observed_delta_workload(
    app_services: Services,
) -> None:
    event = UsageEvent(
        connector_id="synthetic-other",
        provider=Provider.HERMES,
        source_id="synthetic-source",
        record_identity="1",
        timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        input_total_tokens=10,
        output_total_tokens=5,
        measurement_type=MeasurementType.DELTA,
        quality=Quality.ESTIMATED,
        parser_version="synthetic-v1",
    )
    app_services.usage_repository.persist_scan(
        [
            event,
            replace(event, record_identity="2", input_total_tokens=None),
            replace(
                event,
                record_identity="3",
                measurement_type=MeasurementType.CUMULATIVE,
                input_total_tokens=1000,
                output_total_tokens=1000,
            ),
        ],
        SyncCursor("synthetic-source", 100, None, "synthetic-v1"),
    )
    summary = app_services.analytics.dashboard()
    assert summary.workload_tokens == 15
    assert summary.input_total_tokens == 10
    assert summary.output_total_tokens == 10
    assert summary.cache_read_tokens is None
    assert summary.event_count == 2
    assert summary.quality_counts[Quality.ESTIMATED] == 2
