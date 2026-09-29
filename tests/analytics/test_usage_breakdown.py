"""The explorer reconciles with observed totals without leaking raw sessions."""

from dataclasses import replace
from datetime import UTC, datetime

from tokenhub.domain.models import (
    MeasurementType,
    Provider,
    Quality,
    SyncCursor,
    UsageEvent,
)

from tests.service_support import Services


def event(source: str, identity: str, model: str | None, session: str) -> UsageEvent:
    return UsageEvent(
        connector_id="claude-code-local", provider=Provider.CLAUDE_CODE,
        source_id=source, record_identity=identity, timestamp=datetime(2026, 9, 27, tzinfo=UTC),
        input_total_tokens=100, output_total_tokens=25, cache_read_tokens=70,
        measurement_type=MeasurementType.DELTA, quality=Quality.EXACT, parser_version="test",
        model_name=model, session_id=session, model_attribution="message",
    )


def test_usage_groups_deduplicate_claude_history_and_keep_unknowns(app_services: Services) -> None:
    usage = app_services.usage_repository
    usage.persist_scan([
        event("a", "message-1", "model-a", "private-session-a"),
        event("a", "message-2", None, "private-session-a"),
    ], SyncCursor("a", 0, None, "test"))
    usage.persist_scan([
        event("b", "message-1", "model-a", "copied-history"),
        replace(event("b", "message-3", "model-b", "private-session-b"), input_total_tokens=None),
        replace(event("b", "snapshot", "model-c", "private-session-b"), measurement_type=MeasurementType.CUMULATIVE),
    ], SyncCursor("b", 0, None, "test"))
    result = app_services.analytics.usage_breakdown()
    assert result["totals"]["workload_tokens"] == app_services.analytics.dashboard().workload_tokens == 250
    assert result["totals"]["event_count"] == 3
    assert result["totals"]["session_count"] == 2
    assert result["totals"]["model_count"] == 2
    assert sum(row["workload_tokens"] or 0 for row in result["models"]) == 250
    assert {row["model_name"] for row in result["models"]} == {"model-a", "model-b", None}
    unknown_input = next(row for row in result["models"] if row["model_name"] == "model-b")
    assert unknown_input["input_total_tokens"] is None
    assert unknown_input["workload_tokens"] is None
    assert unknown_input["output_total_tokens"] == 25
    assert unknown_input["cache_write_tokens"] is None
    assert "private-session" not in str(result)
    assert "copied-history" not in str(result)


def test_rescan_backfills_metadata_without_changing_counts(app_services: Services) -> None:
    usage = app_services.usage_repository
    cursor = SyncCursor("a", 0, None, "test")
    original = event("a", "one", None, "session")
    usage.persist_scan([original], cursor)
    outcome = usage.persist_scan([replace(original, model_name="new-model", input_total_tokens=999)], cursor)
    assert outcome.inserted_events == 0
    assert usage.dashboard_totals().workload_tokens == 125
    assert app_services.analytics.usage_breakdown()["models"][0]["model_name"] == "new-model"


def test_canonical_message_prefers_recorded_model_on_equal_counts(app_services: Services) -> None:
    usage = app_services.usage_repository
    usage.persist_scan([event('a', 'same-message', None, 'original')], SyncCursor('a', 0, None, 'test'))
    usage.persist_scan([event('b', 'same-message', 'recorded-model', 'copy')], SyncCursor('b', 0, None, 'test'))
    result = app_services.analytics.usage_breakdown()
    assert result['totals']['workload_tokens'] == 125
    assert result['models'][0]['model_name'] == 'recorded-model'


def test_usage_period_filters_events_before_grouping(app_services: Services) -> None:
    usage = app_services.usage_repository
    usage.persist_scan([
        replace(event('a', 'yesterday', 'older-model', 'first'), timestamp=datetime(2026, 9, 28, 18, tzinfo=UTC)),
        replace(event('a', 'today', 'today-model', 'second'), timestamp=datetime(2026, 9, 29, 2, tzinfo=UTC)),
        replace(event('a', 'tomorrow', 'future-model', 'third'), timestamp=datetime(2026, 9, 30, 2, tzinfo=UTC)),
    ], SyncCursor('a', 0, None, 'test'))
    result = app_services.analytics.usage_breakdown(
        datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )
    assert result['totals']['workload_tokens'] == 125
    assert result['totals']['session_count'] == 1
    assert [row['model_name'] for row in result['models']] == ['today-model']
    assert [row['provider'] for row in result['providers']] == ['claude_code']


def test_usage_period_preserves_unknown_empty_totals(app_services: Services) -> None:
    result = app_services.analytics.usage_breakdown(
        datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )
    assert result['totals']['workload_tokens'] is None
    assert result['totals']['event_count'] == 0
