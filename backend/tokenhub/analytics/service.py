"""SQL summaries and bounded views of canonical observed usage."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, or_, select

from tokenhub.database.repositories import UsageRepository
from tokenhub.domain.models import DashboardSummary

_COUNTERS = ("input_total_tokens", "output_total_tokens", "cache_read_tokens",
             "cache_write_tokens", "reasoning_tokens")
_AGENTS = {"codex": "Codex", "claude_code": "Claude Code", "hermes": "Hermes Agent",
           "vscode_copilot": "VS Code Copilot", "antigravity": "Antigravity"}


def _project(row: Any) -> dict[str, Any]:
    values = dict(row)
    for key in ("first_seen", "last_seen"):
        stamp = values.get(key)
        if stamp is not None:
            values[key] = stamp.replace(tzinfo=UTC).isoformat()
    return values


class AnalyticsService:
    def __init__(self, usage_repository: UsageRepository) -> None:
        self.usage_repository = usage_repository

    def dashboard(self) -> DashboardSummary:
        return self.usage_repository.dashboard_totals()

    def _events(self, start: datetime | None, end: datetime | None) -> Any:
        events, delta = self.usage_repository._canonical_deltas()
        query = select(events).where(delta)
        if start is not None:
            query = query.where(events.timestamp >= start.astimezone(UTC).replace(tzinfo=None))
        if end is not None:
            query = query.where(events.timestamp < end.astimezone(UTC).replace(tzinfo=None))
        return query.subquery().c

    @staticmethod
    def _key(events: Any) -> Any:
        return func.tokenhub_session_key(events.connector_id, events.session_id, events.source_id)

    def _summary(self, events: Any) -> list[Any]:
        return [*(func.sum(events[field]).label(field) for field in _COUNTERS),
                func.sum(events.input_total_tokens + events.output_total_tokens).label("workload_tokens"),
                func.count().label("event_count"),
                func.count(func.distinct(events.connector_id + "\0" + func.coalesce(func.nullif(events.session_id, ""), events.source_id))).label("session_count"),
                func.count(func.distinct(events.model_name)).label("model_count"),
                func.coalesce(func.sum(case((or_(events.input_total_tokens.is_(None),
                    events.output_total_tokens.is_(None)), 1), else_=0)), 0).label("incomplete_event_count"),
                func.min(events.timestamp).label("first_seen"), func.max(events.timestamp).label("last_seen")]

    def usage_breakdown(self, start: datetime | None = None, end: datetime | None = None,
                        *, summary_only: bool = False) -> dict[str, Any]:
        events = self._events(start, end)
        session = self.usage_repository.session
        with session.begin():
            totals = _project(session.execute(select(*self._summary(events))).mappings().one())
            providers = [_project(row) for row in session.execute(
                select(events.provider, *self._summary(events)).group_by(events.provider)
                    .order_by(func.sum(events.input_total_tokens + events.output_total_tokens).desc(), events.provider)
            ).mappings()]
            if summary_only:
                unknown = {provider: count for provider, count in session.execute(select(events.provider, func.count())
                    .where(events.model_name.is_(None)).group_by(events.provider))}
                return {"totals": totals, "providers": providers, "models": [], "sessions": [], "paging": True,
                        "unknown_model_events": unknown}
            models = self._model_rows(events)
            sessions = [_project(row) for row in session.execute(self._session_query(events)).mappings()]
            groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in session.execute(select(events.provider, self._key(events).label("session_key"),
                    events.model_name, *self._summary(events)).group_by(events.provider,
                    self._key(events), events.model_name)).mappings():
                model = _project(row)
                provider, key = model.pop("provider"), model.pop("session_key")
                groups.setdefault((provider, key), []).append(model)
            for item in sessions:
                item["models"] = groups.get((item["provider"], item["session_key"]), [])
            return {"totals": totals, "providers": providers, "models": models, "sessions": sessions}

    def _model_query(self, events: Any) -> Any:
        attribution = case((func.count(func.distinct(events.model_attribution)) == 1,
                            func.min(events.model_attribution)), else_="mixed").label("attribution")
        return select(events.provider, events.model_name, attribution, *self._summary(events)).group_by(
            events.provider, events.model_name)

    def _model_rows(self, events: Any) -> list[dict[str, Any]]:
        return [_project(row) for row in self.usage_repository.session.execute(self._model_query(events)
            .order_by(func.sum(events.input_total_tokens + events.output_total_tokens).desc(),
                      events.provider, events.model_name)).mappings()]

    def _session_query(self, events: Any) -> Any:
        return select(events.provider, self._key(events).label("session_key"), *self._summary(events)).group_by(
            events.provider, self._key(events)).order_by(
                func.sum(events.input_total_tokens + events.output_total_tokens).desc(),
                events.provider, self._key(events))

    def usage_page(self, kind: str, *, start: datetime | None = None, end: datetime | None = None,
                   provider: str | None = None, query: str = "", sort: str = "workload_tokens",
                   direction: str = "descending", offset: int = 0, limit: int = 25,
                   model: str | None = None, unknown_model: bool = False,
                   session_key: str | None = None) -> dict[str, Any]:
        events = self._events(start, end)
        if provider:
            events = select(events).where(events.provider == provider).subquery().c
        full_events = events
        full_sessions = self._session_query(full_events).order_by(None).subquery().c
        if kind == "detail":
            events = select(events).where(self._key(events) == session_key).subquery().c
        if model is not None or unknown_model:
            events = select(events).where(events.model_name.is_(None) if unknown_model
                                         else events.model_name == model).subquery().c
        if kind == "sessions" and query.strip():
            text = query.strip().casefold()
            agent = case(_AGENTS, value=events.provider, else_=events.provider)
            matching = select(self._key(events)).where(or_(
                func.instr(func.lower(func.coalesce(events.model_name, "Model not recorded")), text) > 0,
                func.instr(func.lower(agent), text) > 0,
                func.instr(self._key(events), text) > 0))
            events = select(events).where(self._key(events).in_(matching)).subquery().c
        grouped = (self._session_query(events) if kind == "sessions" else self._model_query(events)).order_by(None).subquery()
        page = select(grouped)
        if kind != "sessions" and query.strip():
            text = query.strip().casefold()
            agent = case(_AGENTS, value=grouped.c.provider, else_=grouped.c.provider)
            page = page.where(or_(func.instr(func.lower(func.coalesce(grouped.c.model_name, "Model not recorded")), text) > 0,
                                  func.instr(func.lower(agent), text) > 0))
        filtered = page.subquery()
        fields = filtered.c
        identity = (fields.session_key if kind == "sessions" else fields.model_name).collate("TOKENHUB_NATURAL")
        metric = identity if sort == "identity" else fields[sort]
        if sort == "model_count" and kind == "sessions":
            metric = full_sessions.model_count
        page = select(filtered)
        if kind == "sessions" and (model is not None or unknown_model or sort == "model_count"):
            page = page.join(full_sessions.table, (full_sessions.provider == fields.provider)
                & (full_sessions.session_key == fields.session_key))
        page = page.order_by(metric.is_(None), metric.asc() if direction == "ascending" else metric.desc(),
                             identity, fields.provider).offset(offset).limit(limit)
        session = self.usage_repository.session
        with session.begin():
            total = session.scalar(select(func.count()).select_from(filtered)) or 0
            items = [_project(row) for row in session.execute(page).mappings()]
            if kind == "sessions":
                full_rows = {(row["provider"], row["session_key"]): _project(row) for row in session.execute(
                    select(full_sessions).where(full_sessions.session_key.in_([row["session_key"] for row in items]))
                ).mappings()} if items else {}
                for index, row in enumerate(items):
                    full = full_rows[(row["provider"], row["session_key"])]
                    full["models"] = []  # Fetch model contributions only when expanded.
                    if model is not None or unknown_model:
                        full["contribution"] = row
                    items[index] = full
            return {"items": items, "total": total, "offset": offset, "limit": limit}
