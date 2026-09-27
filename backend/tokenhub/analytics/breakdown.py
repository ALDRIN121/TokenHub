"""Agent, model, and session views of the canonical observed workload."""

import hashlib
from collections import defaultdict
from datetime import UTC
from typing import Any

from tokenhub.database.models import UsageEventRecord

_COUNTERS = (
    "input_total_tokens", "output_total_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens",
)


def _session_key(event: UsageEventRecord) -> str:
    # Stable across refreshes, with no original session id or filesystem path.
    identity = f"{event.connector_id}\0{event.session_id or event.source_id}"
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def _summary(events: list[UsageEventRecord]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _COUNTERS:
        values = [getattr(event, field) for event in events if getattr(event, field) is not None]
        result[field] = sum(values) if values else None
    workloads = [
        event.input_total_tokens + event.output_total_tokens
        for event in events
        if event.input_total_tokens is not None and event.output_total_tokens is not None
    ]
    result.update(
        workload_tokens=sum(workloads) if workloads else None,
        event_count=len(events),
        session_count=len({_session_key(event) for event in events}),
        model_count=len({event.model_name for event in events if event.model_name is not None}),
        incomplete_event_count=sum(
            event.input_total_tokens is None or event.output_total_tokens is None for event in events
        ),
        first_seen=min((event.timestamp for event in events), default=None),
        last_seen=max((event.timestamp for event in events), default=None),
    )
    for field in ("first_seen", "last_seen"):
        result[field] = result[field].replace(tzinfo=UTC).isoformat() if result[field] else None
    return result


def usage_breakdown(events: list[UsageEventRecord]) -> dict[str, Any]:
    providers: dict[str, list[UsageEventRecord]] = defaultdict(list)
    models: dict[tuple[str, str | None], list[UsageEventRecord]] = defaultdict(list)
    sessions: dict[tuple[str, str], list[UsageEventRecord]] = defaultdict(list)
    for event in events:
        providers[event.provider].append(event)
        models[event.provider, event.model_name].append(event)
        sessions[event.provider, _session_key(event)].append(event)
    model_rows = []
    for (provider, model), group in models.items():
        attributions = {event.model_attribution for event in group}
        model_rows.append(dict(
            _summary(group), provider=provider, model_name=model,
            attribution=next(iter(attributions)) if len(attributions) == 1 else "mixed",
        ))
    session_rows = []
    for (provider, key), group in sessions.items():
        session_models: dict[str | None, list[UsageEventRecord]] = defaultdict(list)
        for event in group:
            session_models[event.model_name].append(event)
        session_rows.append(dict(
            _summary(group), provider=provider, session_key=key,
            models=[dict(_summary(rows), model_name=model) for model, rows in session_models.items()],
        ))
    sort_key = lambda row: (-(row["workload_tokens"] or 0), row["provider"], row.get("model_name") or "")
    return {
        "totals": _summary(events),
        "providers": sorted([dict(_summary(group), provider=provider) for provider, group in providers.items()], key=sort_key),
        "models": sorted(model_rows, key=sort_key),
        "sessions": sorted(session_rows, key=sort_key),
    }
