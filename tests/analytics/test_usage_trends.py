"""Calendar trends use the same canonical observations as the dashboard."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tokenhub.domain.models import Provider, SyncCursor

from tests.analytics.test_usage_breakdown import event


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def seed(services, rows):
    for source in {row.source_id for row in rows}:
        services.usage_repository.persist_scan([row for row in rows if row.source_id == source], SyncCursor(source, 0, None, "test"))


def history(services):
    seed(services, [
        replace(event("a", "previous-a", "old-model", "old-a"), timestamp=stamp("2026-09-20T12:00Z"), input_total_tokens=10, output_total_tokens=5),
        replace(event("a", "previous-b", "model-a", "old-b"), timestamp=stamp("2026-09-25T12:00Z")),
        event("a", "current-a", "model-a", "new-a"),
        replace(event("a", "current-b", None, "new-b"), timestamp=stamp("2026-09-28T12:00Z"), input_total_tokens=200, output_total_tokens=50),
        replace(event("a", "current-c", "model-a", "new-c"), timestamp=stamp("2026-10-02T12:00Z"), input_total_tokens=60, output_total_tokens=15),
    ])


def test_daily_trends_and_breakdowns_reconcile_with_period_totals(app_services):
    history(app_services)
    result = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-10-04T00:00Z"), dimension="models")
    assert result["current"]["workload_tokens"] == 450
    assert result["previous"]["workload_tokens"] == 140
    assert result["period"]["previous_from"] == "2026-09-20T00:00:00+00:00"
    assert len(result["buckets"]) == 7
    assert [row["current"]["workload_tokens"] for row in result["buckets"]] == [125, 250, None, None, None, 75, None]
    assert sum(row["current"]["workload_tokens"] or 0 for row in result["buckets"]) == 450
    assert sum(row["previous"]["workload_tokens"] or 0 for row in result["buckets"]) == 140
    assert sum(row["current"]["workload_tokens"] or 0 for row in result["breakdown"]["items"]) == 450
    assert sum(row["previous"]["workload_tokens"] or 0 for row in result["breakdown"]["items"]) == 140
    assert {row["model_name"] for row in result["breakdown"]["items"]} == {"old-model", "model-a", None}
    change = result["comparison"]["workload_tokens"]
    assert change["difference"] == 310
    assert change["percent_change"] == pytest.approx(221.428571)
    assert "old-a" not in str(result)


def test_monday_weeks_clip_edges_and_align_previous_intervals(app_services):
    history(app_services)
    result = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-10-04T00:00Z"), granularity="week")
    assert [row["from"] for row in result["buckets"]] == ["2026-09-27", "2026-09-28"]
    assert [row["to"] for row in result["buckets"]] == ["2026-09-28", "2026-10-04"]
    assert [row["current"]["workload_tokens"] for row in result["buckets"]] == [125, 325]
    assert [row["previous"]["workload_tokens"] for row in result["buckets"]] == [15, 125]
    assert result["buckets"][1]["previous_from"] == "2026-09-21"


def test_dst_comparison_uses_calendar_days_and_exclusive_end(app_services):
    seed(app_services, [replace(event("a", str(i), "model-a", str(i)), timestamp=stamp(value)) for i, value in enumerate([
        "2026-03-08T04:30Z", "2026-03-08T05:00Z", "2026-03-09T03:30Z", "2026-03-09T04:00Z", "2026-03-10T04:00Z",
    ])])
    result = app_services.analytics.usage_trends(start=stamp("2026-03-08T05:00Z"), end=stamp("2026-03-10T04:00Z"), time_zone="America/New_York")
    assert result["period"]["days"] == 2
    assert stamp(result["period"]["previous_from"]) == stamp("2026-03-06T05:00Z")
    assert result["current"]["workload_tokens"] == 375
    assert [row["current"]["workload_tokens"] for row in result["buckets"]] == [250, 125]
    assert [row["previous"]["event_count"] for row in result["buckets"]] == [0, 1]


def test_fractional_offset_buckets_follow_local_dates(app_services):
    seed(app_services, [replace(event("a", "india", "model-a", "one"), timestamp=stamp("2026-09-26T23:30Z"))])
    result = app_services.analytics.usage_trends(start=stamp("2026-09-26T18:30Z"), end=stamp("2026-09-27T18:30Z"), time_zone="Asia/Kolkata")
    assert result["buckets"][0]["from"] == "2026-09-27"
    assert result["current"]["workload_tokens"] == 125
    assert result["comparison"]["workload_tokens"]["percent_change"] is None
    assert result["comparison"]["workload_tokens"]["state"] == "no_baseline"


def test_all_time_reconciles_dashboard_without_inventing_a_previous_period(app_services):
    seed(app_services, [event("a", "dated", "m", "one"), replace(event("a", "second", None, "two"), timestamp=stamp("2026-09-28T12:00Z"))])
    result = app_services.analytics.usage_trends()
    assert result["current"]["workload_tokens"] == app_services.analytics.dashboard().workload_tokens == 250
    assert sum(row["current"]["workload_tokens"] or 0 for row in result["buckets"]) == 250
    assert result["previous"] is None
    assert result["comparison"]["workload_tokens"]["state"] == "all_time"
    filtered = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-09-28T00:00Z"))
    assert filtered["current"]["workload_tokens"] == 125


def test_unknown_partial_and_zero_baselines_do_not_produce_fake_changes(app_services):
    seed(app_services, [
        event("a", "complete", "m", "one"),
        replace(event("a", "partial", "m", "two"), input_total_tokens=None),
        replace(event("a", "baseline", "m", "old"), timestamp=stamp("2026-09-26T12:00Z"), input_total_tokens=0, output_total_tokens=0),
    ])
    result = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-09-28T00:00Z"))
    assert result["current"]["workload_tokens"] == 125
    assert result["current"]["reported_counts"]["workload_tokens"] == 1
    assert result["comparison"]["workload_tokens"]["state"] == "incomplete"
    assert result["comparison"]["workload_tokens"]["percent_change"] is None
    assert result["comparison"]["workload_tokens"]["difference"] is None
    assert result["current"]["cache_write_tokens"] is None
    assert result["comparison"]["cache_write_tokens"]["state"] == "incomplete"


def test_model_provider_and_unknown_filters_apply_after_canonical_selection(app_services):
    seed(app_services, [
        event("a", "same", "inside", "one"),
        replace(event("b", "same", "outside", "copy"), timestamp=stamp("2026-09-25T12:00Z"), input_total_tokens=300),
        replace(event("a", "codex", "inside", "two"), connector_id="codex-local", provider=Provider.CODEX),
        replace(event("a", "unknown", None, "three"), connector_id="codex-local", provider=Provider.CODEX),
    ])
    result = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-09-28T00:00Z"), model="inside", dimension="models")
    assert result["current"]["workload_tokens"] == 125
    assert result["current"]["event_count"] == 1
    assert result["breakdown"]["items"][0]["provider"] == "codex"
    unknown = app_services.analytics.usage_trends(provider="codex", unknown_model=True)
    assert unknown["current"]["workload_tokens"] == 125
    assert unknown["current"]["model_count"] == 0


def test_empty_history_and_long_history_keep_bounded_and_explicit_results(app_services):
    empty = app_services.analytics.usage_trends()
    assert empty["current"]["event_count"] == 0
    assert empty["current"]["workload_tokens"] is None
    assert empty["buckets"] == []
    seed(app_services, [replace(event("a", "ancient", "m", "one"), timestamp=datetime(2000, 1, 1, tzinfo=UTC)), event("a", "new", "m", "two")])
    result = app_services.analytics.usage_trends()
    assert result["series_limited"] is True
    assert result["buckets"] == []
    assert result["current"]["workload_tokens"] == 250


def test_previous_only_models_remain_paged_with_provider_identity(app_services):
    seed(app_services, [replace(event("a", str(i), f"model-{i}", str(i)), timestamp=stamp("2026-09-26T12:00Z")) for i in range(30)])
    first = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-09-28T00:00Z"), dimension="models", limit=25)
    second = app_services.analytics.usage_trends(start=stamp("2026-09-27T00:00Z"), end=stamp("2026-09-28T00:00Z"), dimension="models", offset=25, limit=25)
    assert first["breakdown"]["total"] == second["breakdown"]["total"] == 30
    assert len(first["breakdown"]["items"]) == 25
    assert len(second["breakdown"]["items"]) == 5
    assert first["breakdown"]["items"][0]["current"]["event_count"] == 0
    assert first["breakdown"]["items"][0]["comparison"]["workload_tokens"]["percent_change"] == -100
