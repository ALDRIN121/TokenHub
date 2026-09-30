"""Bounded, paired calendar views over the explorer's canonical observations."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, literal, or_, select, union_all

from tokenhub.analytics.calendar import bucket_date, midnight
from tokenhub.analytics.service import _COUNTERS, _project

if TYPE_CHECKING:
    from tokenhub.analytics.service import AnalyticsService

METRICS = ("workload_tokens", *_COUNTERS)


def empty_totals() -> dict[str, Any]:
    return {**dict.fromkeys(METRICS), "event_count": 0, "session_count": 0,
            "model_count": 0, "incomplete_event_count": 0, "first_seen": None,
            "last_seen": None, "reported_counts": dict.fromkeys(METRICS, 0)}


def fields(analytics: "AnalyticsService", events: Any) -> list[Any]:
    return [*analytics._summary(events), *(func.count(
        events.input_total_tokens + events.output_total_tokens if metric == "workload_tokens"
        else events[metric]).label(f"reported_{metric}") for metric in METRICS)]


def project(row: Any) -> dict[str, Any]:
    result = _project(row)
    result["reported_counts"] = {metric: result.pop(f"reported_{metric}") for metric in METRICS}
    return result


def comparisons(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    result = {}
    for metric in METRICS:
        change: dict[str, Any] = {"difference": None, "percent_change": None, "state": "all_time"}
        if previous is not None:
            incomplete = any(row["reported_counts"][metric] < row["event_count"] for row in (current, previous))
            if incomplete:
                change["state"] = "incomplete"
            else:
                now, before = current[metric] or 0, previous[metric] or 0
                change.update(difference=now - before, state="no_baseline" if before == 0 else "compared",
                              percent_change=(now - before) / before * 100 if before else None)
        result[metric] = change
    return result


def bounded(events: Any, start: datetime | None, end: datetime | None) -> Any:
    query = select(events)
    if start is not None:
        query = query.where(events.timestamp >= start.astimezone(UTC).replace(tzinfo=None))
    if end is not None:
        query = query.where(events.timestamp < end.astimezone(UTC).replace(tzinfo=None))
    return query.subquery().c


def usage_trends(analytics: "AnalyticsService", *, start: datetime | None = None,
                 end: datetime | None = None, time_zone: str = "UTC", granularity: str = "day",
                 provider: str | None = None, model: str | None = None, unknown_model: bool = False,
                 dimension: str = "providers", offset: int = 0, limit: int = 25) -> dict[str, Any]:
    try:
        zone = ZoneInfo(time_zone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("Unknown time zone") from error
    if (start is None) != (end is None):
        raise ValueError("Supply both date bounds")
    if model is not None and unknown_model:
        raise ValueError("Choose a recorded model or unknown models, not both")
    if start is not None and end is not None:
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise ValueError("Date bounds require ordered timezone-aware instants")
        if any(bound != midnight(bound.astimezone(zone).date(), zone) for bound in (start, end)):
            raise ValueError("Trend bounds must be local calendar-day boundaries")
    events = analytics._events(None, None)
    query = select(events)
    if provider:
        query = query.where(events.provider == provider)
    if model is not None or unknown_model:
        query = query.where(events.model_name.is_(None) if unknown_model else events.model_name == model)
    events = query.cte("trend_events").c
    session = analytics.usage_repository.session
    with session.begin():
        return _snapshot(analytics, events, start, end, zone, granularity, dimension, offset, limit)


def _snapshot(analytics: "AnalyticsService", events: Any, start: datetime | None, end: datetime | None,
              zone: ZoneInfo, granularity: str, dimension: str, offset: int, limit: int) -> dict[str, Any]:
    session = analytics.usage_repository.session
    all_time = start is None
    current_events = bounded(events, start, end)
    current = project(session.execute(select(*fields(analytics, current_events))).mappings().one())
    if all_time:
        first, last = session.execute(select(func.min(events.timestamp), func.max(events.timestamp))).one()
        start = midnight(first.replace(tzinfo=UTC).astimezone(zone).date(), zone) if first else None
        end = midnight(last.replace(tzinfo=UTC).astimezone(zone).date() + timedelta(days=1), zone) if last else None
    days = (end.astimezone(zone).date() - start.astimezone(zone).date()).days if start and end else 0
    try:
        previous_start = midnight(start.astimezone(zone).date() - timedelta(days=days), zone) if start and not all_time else None
    except OverflowError as error:
        raise ValueError("Date range is outside supported calendar bounds") from error
    previous_events = bounded(events, previous_start, start) if previous_start else None
    previous = project(session.execute(select(*fields(analytics, previous_events))).mappings().one()) if previous_events is not None else None
    current_query = select(current_events, literal("current").label("period"))
    paired = (union_all(current_query, select(previous_events, literal("previous").label("period")))
              if previous_events is not None else current_query).subquery().c
    bucket_count = days if granularity == "day" else (days + (start.astimezone(zone).weekday() if start else 0) + 6) // 7
    series_limited = bucket_count > (366 if granularity == "day" else 260)
    buckets = _buckets(analytics, paired, start, end, zone, granularity, days, all_time) if start and end and not series_limited else []
    return {"period": {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None,
                       "previous_from": previous_start.isoformat() if previous_start else None,
                       "previous_to": start.isoformat() if previous_start and start else None,
                       "days": days, "time_zone": zone.key, "granularity": granularity,
                       "all_time": all_time, "includes_today": bool(start and end and start <= datetime.now(UTC) < end)},
            "current": current, "previous": previous,
            "comparison": comparisons(current, previous), "buckets": buckets,
            "series_limited": series_limited,
            "breakdown": _breakdown(analytics, paired, all_time, dimension, offset, limit)}


def _buckets(analytics: "AnalyticsService", events: Any, start: datetime, end: datetime,
             zone: ZoneInfo, granularity: str, days: int, all_time: bool) -> list[dict[str, Any]]:
    bucket = func.tokenhub_trend_bucket(events.timestamp, zone.key, granularity,
        case((events.period == "previous", days), else_=0))
    rows = analytics.usage_repository.session.execute(select(events.period, bucket.label("bucket"),
        *fields(analytics, events)).where(events.timestamp.is_not(None)).group_by(events.period, bucket)).mappings()
    grouped = {}
    for row in rows:
        totals = project(row)
        totals.pop("period")
        totals.pop("bucket")
        grouped[(row["period"], row["bucket"])] = totals
    first, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    cursor = bucket_date(first, granularity)
    result = []
    while cursor < last:
        next_day = cursor + timedelta(days=7 if granularity == "week" else 1)
        lower, upper = max(first, cursor), min(last, next_day)
        result.append({"from": lower.isoformat(), "to": upper.isoformat(),
            "previous_from": (lower - timedelta(days=days)).isoformat() if not all_time else None,
            "previous_to": (upper - timedelta(days=days)).isoformat() if not all_time else None,
            "current": grouped.get(("current", cursor.isoformat()), empty_totals()),
            "previous": grouped.get(("previous", cursor.isoformat()), empty_totals()) if not all_time else None})
        cursor = next_day
    return result


def _breakdown(analytics: "AnalyticsService", events: Any, all_time: bool,
               dimension: str, offset: int, limit: int) -> dict[str, Any]:
    session = analytics.usage_repository.session
    identities = [events.provider, events.model_name] if dimension == "models" else [events.provider]
    query = select(*identities).group_by(*identities)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    keys = list(session.execute(query.order_by(func.sum(events.input_total_tokens + events.output_total_tokens).desc(),
        *[column.collate("TOKENHUB_NATURAL") for column in identities]).offset(offset).limit(limit)))
    if not keys:
        return {"dimension": dimension, "items": [], "total": total, "offset": offset, "limit": limit}
    conditions = [((events.provider == key[0]) &
        (events.model_name.is_(None) if key[1] is None else events.model_name == key[1]))
        if dimension == "models" else events.provider == key[0] for key in keys]
    rows = session.execute(select(*identities, events.period, *fields(analytics, events))
        .where(or_(*conditions)).group_by(*identities, events.period)).mappings()
    grouped = {}
    for row in rows:
        values = project(row)
        identity = (values.pop("provider"), values.pop("model_name") if dimension == "models" else None)
        period = values.pop("period")
        grouped[(*identity, period)] = values
    items = []
    for key in keys:
        provider, model = key[0], key[1] if dimension == "models" else None
        current = grouped.get((provider, model, "current"), empty_totals())
        previous = grouped.get((provider, model, "previous"), empty_totals()) if not all_time else None
        items.append({"provider": provider, "model_name": model, "current": current,
                      "previous": previous, "comparison": comparisons(current, previous)})
    return {"dimension": dimension, "items": items, "total": total, "offset": offset, "limit": limit}
