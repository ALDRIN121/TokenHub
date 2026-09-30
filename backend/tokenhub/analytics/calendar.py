"""Local calendar buckets, shared by SQL grouping and interval labels."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def midnight(day: date, zone: ZoneInfo) -> datetime:
    # Converting to UTC also resolves zones whose spring gap spans midnight.
    return datetime.combine(day, time.min, zone).astimezone(UTC)


def bucket_date(day: date, granularity: str) -> date:
    return day - timedelta(days=day.weekday()) if granularity == "week" else day


def sqlite_bucket(stamp: str | None, time_zone: str, granularity: str, shift_days: int) -> str | None:
    if stamp is None:
        return None
    local = datetime.fromisoformat(stamp).replace(tzinfo=UTC).astimezone(ZoneInfo(time_zone))
    return bucket_date(local.date() + timedelta(days=shift_days), granularity).isoformat()
