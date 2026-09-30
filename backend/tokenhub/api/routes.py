"""Thin, versioned HTTP routes over the TokenHub service layer.

Routes own transport concerns only — status codes, JSON projection, and error
mapping. All persistence, discovery, parsing, and aggregation stay in the
services so the local API cannot drift from the tested behavior.

Every projection here is deliberately path-free and record-free: the HTTP
surface exposes identifiers, states, counts, and timestamps, never a canonical
path, approved root, provider path, credential, prompt, or raw record.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from tokenhub.api.container import CollectionBusyError, Container

# The service layer's own return type for an approval. The HTTP layer only
# reads it — it never queries the ORM itself — so this is a data dependency on
# the service contract, not a persistence leak into transport code.
from tokenhub.database.models import SourceRecord
from tokenhub.domain.models import (
    APPROVED_SOURCE_STATES,
    DashboardSummary,
    ImportOutcome,
    RebuildOutcome,
    SourceState,
)
from tokenhub.ingestion.service import (
    SourceNotApprovedError,
    SourceNotFoundError,
    UnsupportedSourceError,
)
from tokenhub.security.redaction import redact_sensitive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_ERROR_MAP: tuple[tuple[type[Exception], int, str], ...] = (
    (CollectionBusyError, 429, "Collection is busy; try again shortly"),
    (SourceNotFoundError, 404, "Unknown source"),
    (SourceNotApprovedError, 409, "Source is not approved"),
    (UnsupportedSourceError, 422, "Source is not supported"),
    (ValueError, 400, "Invalid source path"),
    (OSError, 400, "Source is unavailable"),
)


def container_for(request: Request) -> Container:
    """The app's container, injected by the factory."""
    return request.app.state.container


def _service_failure(error: Exception) -> HTTPException:
    """Map a service exception to a status code and a path-free message.

    Known service failures get a fixed, path-free detail. Anything else becomes
    a generic internal error: a route must never hand a provider path, a raw
    record, or a credential to the client, and the log line goes through the
    central redactor.
    """
    for error_type, status, detail in _ERROR_MAP:
        if isinstance(error, error_type):
            return HTTPException(status_code=status, detail=redact_sensitive(detail))
    logger.error("unhandled TokenHub service failure: %s", redact_sensitive(str(error)))
    return HTTPException(status_code=500, detail="Internal error")


def _freshness_payload(summary: DashboardSummary) -> list[dict[str, object]]:
    return [
        {
            "source_id": item.source_id,
            "state": item.state.value,
            "latest_event_at": (
                item.latest_event_at.isoformat()
                if item.latest_event_at is not None
                else None
            ),
            "unsupported_records": item.unsupported_records,
        }
        for item in summary.source_freshness
    ]


def _quality_payload(summary: DashboardSummary) -> dict[str, int]:
    return {quality.value: count for quality, count in summary.quality_counts.items()}


def _summary_payload(summary: DashboardSummary) -> dict[str, object]:
    return {
        "workload_tokens": summary.workload_tokens,
        "input_total_tokens": summary.input_total_tokens,
        "output_total_tokens": summary.output_total_tokens,
        "cache_read_tokens": summary.cache_read_tokens,
        "cache_write_tokens": summary.cache_write_tokens,
        "reasoning_tokens": summary.reasoning_tokens,
        "event_count": summary.event_count,
        "quality_counts": _quality_payload(summary),
        "source_freshness": _freshness_payload(summary),
    }


def _import_payload(outcome: ImportOutcome) -> dict[str, object]:
    return {
        "inserted_events": outcome.inserted_events,
        "duplicate_events": outcome.duplicate_events,
        "partial_final_record": outcome.partial_final_record,
        "unsupported_records": outcome.unsupported_records,
    }


def _approval_payload(source: SourceRecord) -> dict[str, object]:
    return {
        "source_id": source.source_id,
        "provider": source.provider,
        "display_name": source.display_name,
        "state": source.state,
    }


@router.get("/status")
def status() -> dict[str, str]:
    """Liveness for the local UI and for startup checks."""
    return {"status": "ok"}


@router.get("/discovery")
def discovery(request: Request, include_sources: bool = True) -> dict[str, object]:
    """Report detected providers. Presence only — no source file is read."""
    container = container_for(request)
    results = container.discovery_results()
    with container.read_services() as services:
        sources = services.source_repository._existing_sources(
            [source.source_id for result in results for source in result.sources]
        )
        results = [result.model_copy(update={"sources": tuple(
            source.model_copy(update={"state": SourceState(sources[source.source_id].state)})
            if source.source_id in sources else source for source in result.sources
        )}) for result in results]
    return {
        "providers": [
            {
                "connector_id": result.connector_id,
                "display_name": result.display_name,
                "provider": result.provider.value if result.provider is not None else None,
                "state": result.state.value,
                "confidence": result.confidence.value,
                "evidence_codes": list(result.evidence_codes),
                "sources": [source.model_dump(mode="json") for source in result.sources] if include_sources else [],
                **({"source_count": len(result.sources),
                    "supported_source_count": sum(source.scan_supported for source in result.sources),
                    "approved_count": sum(source.scan_supported and source.state in APPROVED_SOURCE_STATES for source in result.sources),
                    "awaiting_approval": sum(source.scan_supported and source.state == SourceState.DISCOVERED for source in result.sources)} if not include_sources else {}),
            }
            for result in results
        ]
    }


@router.get("/collection")
def collection_status(request: Request) -> dict[str, object]:
    container = container_for(request)
    return container.collection_status()


@router.get("/jobs/{job_id}")
def collection_job(job_id: str, request: Request) -> dict[str, object]:
    try:
        return container_for(request).job_status(job_id)
    except SourceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Unknown job") from error


def _operation(container: Container, kind: str, argument: str | None, background: bool) -> object:
    try:
        if background:
            job = container.submit(kind, argument)
            return JSONResponse(status_code=202, content={"job_id": job.job_id})
        return container.execute(kind, argument)
    except Exception as error:
        raise _service_failure(error) from error


@router.post("/collection/refresh")
def refresh_collection(request: Request, background: bool = False) -> Any:
    container = container_for(request)
    value = _operation(container, "refresh", None, background)
    return value if background else container.collection_status()


def _collection_connector(provider: str) -> str:
    connectors = {
        "codex": "codex-local",
        "claude_code": "claude-code-local",
        "hermes": "hermes-local",
        "vscode_copilot": "vscode-copilot-local",
        "antigravity": "antigravity-local",
    }
    if provider not in connectors:
        raise HTTPException(status_code=422, detail="Source is not supported")
    return connectors[provider]


@router.post("/collection/{provider}/enable")
def enable_provider_collection(provider: str, request: Request, background: bool = False) -> Any:
    container = container_for(request)
    value = _operation(container, "enable", _collection_connector(provider), background)
    return value if background else container.collection_status()


@router.post("/collection/{provider}/disable")
def disable_provider_collection(provider: str, request: Request) -> dict[str, object]:
    container = container_for(request)
    _operation(container, "disable", _collection_connector(provider), False)
    return container.collection_status()


@router.post("/sources/{source_id}/approve")
def approve_source(source_id: str, request: Request) -> dict[str, object]:
    """Approve reading one discovered source; requires a same-origin request."""
    container = container_for(request)
    source = _operation(container, "approve", source_id, False)
    return _approval_payload(cast(SourceRecord, source))


@router.post("/sources/{source_id}/rescan")
def rescan_source(source_id: str, request: Request, background: bool = False) -> Any:
    container = container_for(request)
    value = _operation(container, "rescan", source_id, background)
    return value if background else _import_payload(cast(ImportOutcome, value))


@router.post("/rebuild")
def rebuild(request: Request, background: bool = False) -> Any:
    container = container_for(request)
    value = _operation(container, "rebuild", None, background)
    if background:
        return value
    outcome = cast(RebuildOutcome, value)
    return {"inserted_events": outcome.inserted_events, "failed_source_ids": list(outcome.failed_source_ids)}


@router.get("/dashboard")
def dashboard(request: Request) -> dict[str, object]:
    """Observed delta totals; unknown metrics stay null rather than becoming zero."""
    container = container_for(request)
    with container.read_services() as services:
        summary = services.analytics.dashboard()
    return _summary_payload(summary)


@router.get("/data-quality")
def data_quality(request: Request, lightweight: bool = False,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=100)] = 25) -> dict[str, object]:
    """The same quality and freshness view the dashboard shows."""
    container = container_for(request)
    if lightweight:
        def build() -> dict[str, Any]:
            with container.read_services() as services:
                return services.usage_repository.quality_page(offset, limit)
        return container.cached_view(("quality", offset, limit), build)
    with container.read_services() as services:
        summary = services.analytics.dashboard()
    return {
        "quality_counts": _quality_payload(summary),
        "source_freshness": _freshness_payload(summary),
    }


def _date_bounds(start: datetime | None, end: datetime | None) -> None:
    if any(value is not None and value.utcoffset() is None for value in (start, end)):
        raise HTTPException(status_code=422, detail="Date bounds require a timezone")
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="Date range must end after it begins")


@router.get("/usage")
@router.get("/usage/summary")
def usage(
    request: Request,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    summary_only: bool = False,
) -> dict[str, Any]:
    _date_bounds(start, end)
    container = container_for(request)
    bounded = summary_only or request.url.path.endswith("/summary")
    def build() -> dict[str, Any]:
        with container.read_services() as services:
            return services.analytics.usage_breakdown(start, end, summary_only=bounded)
    if bounded:
        return container.cached_view(("summary", start, end), build)
    return build()  # Preserve the legacy full response for existing clients.


@router.get("/usage/trends")
def usage_trends(
    request: Request,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    time_zone: Annotated[str, Query(max_length=100)] = "UTC",
    granularity: Literal["day", "week"] = "day",
    provider: Literal["codex", "claude_code", "hermes", "vscode_copilot", "antigravity"] | None = None,
    model: Annotated[str | None, Query(max_length=200)] = None,
    unknown_model: bool = False,
    dimension: Literal["providers", "models"] = "providers",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    _date_bounds(start, end)
    container = container_for(request)
    def build() -> dict[str, Any]:
        with container.read_services() as services:
            try:
                return services.analytics.usage_trends(start=start, end=end, time_zone=time_zone,
                    granularity=granularity, provider=provider, model=model, unknown_model=unknown_model,
                    dimension=dimension, offset=offset, limit=limit)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
    return container.cached_view(("trends", start, end, time_zone, granularity, provider,
                                  model, unknown_model, dimension, offset, limit), build)


@router.get("/usage/models")
@router.get("/usage/sessions")
@router.get("/usage/sessions/{session_key}/models")
def usage_page(
    request: Request,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    provider: Literal["codex", "claude_code", "hermes", "vscode_copilot", "antigravity"] | None = None,
    q: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["identity", "workload_tokens", "input_total_tokens", "output_total_tokens",
                  "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "session_count", "model_count"] = "workload_tokens",
    direction: Literal["ascending", "descending"] = "descending",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    model: Annotated[str | None, Query(max_length=200)] = None,
    unknown_model: bool = False,
    session_key: str | None = None,
) -> dict[str, Any]:
    _date_bounds(start, end)
    container = container_for(request)
    kind = "detail" if session_key is not None else request.url.path.rsplit("/", 1)[1]
    def build() -> dict[str, Any]:
        with container.read_services() as services:
            return services.analytics.usage_page(kind, start=start, end=end, provider=provider,
                query=q, sort=sort, direction=direction, offset=offset, limit=limit,
                model=model, unknown_model=unknown_model, session_key=session_key)
    return container.cached_view((kind, start, end, provider, q, sort, direction, offset, limit,
                                  model, unknown_model, session_key), build)
