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
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from tokenhub.api.container import Container

# The service layer's own return type for an approval. The HTTP layer only
# reads it — it never queries the ORM itself — so this is a data dependency on
# the service contract, not a persistence leak into transport code.
from tokenhub.database.models import SourceRecord
from tokenhub.domain.models import (
    DashboardSummary,
    ImportOutcome,
    RebuildOutcome,
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
def discovery(request: Request) -> dict[str, object]:
    """Report detected providers. Presence only — no source file is read."""
    container = container_for(request)
    with container.lock:
        # The collector rediscovers every scan interval and any change bumps
        # data_version, so a poll between changes needs no filesystem walk.
        services = container.services
        results = services.discovery.last_results
        if results is None or container.discovery_version != services.collection.data_version:
            results = services.discovery.discover()
            container.discovery_version = services.collection.data_version
    return {
        "providers": [
            {
                "connector_id": result.connector_id,
                "display_name": result.display_name,
                "provider": result.provider.value if result.provider is not None else None,
                "state": result.state.value,
                "confidence": result.confidence.value,
                "evidence_codes": list(result.evidence_codes),
                "sources": [source.model_dump(mode="json") for source in result.sources],
            }
            for result in results
        ]
    }


@router.get("/collection")
def collection_status(request: Request) -> dict[str, object]:
    container = container_for(request)
    with container.lock:
        return container.services.collection.status(container.settings.scan_interval_seconds)


@router.post("/collection/refresh")
def refresh_collection(request: Request) -> dict[str, object]:
    """Scan approved sources now and return the completed scan time."""
    container = container_for(request)
    try:
        container.collect()
    except Exception as error:
        raise _service_failure(error) from error
    with container.lock:
        return container.services.collection.status(container.settings.scan_interval_seconds)


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
def enable_provider_collection(provider: str, request: Request) -> dict[str, object]:
    """Explicitly include existing and future sources under the discovered root."""
    container = container_for(request)
    connector_id = _collection_connector(provider)
    with container.lock:
        try:
            container.services.collection.enable(connector_id)
        except Exception as error:
            raise _service_failure(error) from error
        return container.services.collection.status(container.settings.scan_interval_seconds)


@router.post("/collection/{provider}/disable")
def disable_provider_collection(provider: str, request: Request) -> dict[str, object]:
    """Stop automatically approving new sources; existing approvals remain."""
    container = container_for(request)
    connector_id = _collection_connector(provider)
    with container.lock:
        container.services.source_repository.disable_auto_import(connector_id)
        container.services.collection.bump()
        return container.services.collection.status(container.settings.scan_interval_seconds)


@router.post("/sources/{source_id}/approve")
def approve_source(source_id: str, request: Request) -> dict[str, object]:
    """Approve reading one discovered source; requires a same-origin request."""
    container = container_for(request)
    with container.lock:
        try:
            source = container.services.ingestion.approve(source_id)
            container.services.collection.bump()
        except Exception as error:  # mapped to a safe status below
            raise _service_failure(error) from error
    return _approval_payload(source)


@router.post("/sources/{source_id}/rescan")
def rescan_source(source_id: str, request: Request) -> dict[str, object]:
    """Import new records from an approved source."""
    container = container_for(request)
    with container.lock:
        try:
            outcome = container.services.ingestion.rescan(source_id)
            container.services.collection.bump()
        except Exception as error:  # mapped to a safe status below
            raise _service_failure(error) from error
    return _import_payload(outcome)


@router.post("/rebuild")
def rebuild(request: Request) -> dict[str, object]:
    """Re-derive normalized usage for every approved source."""
    container = container_for(request)
    with container.lock:
        try:
            outcome: RebuildOutcome = container.services.ingestion.rebuild()
            container.services.collection.bump()
        except Exception as error:  # mapped to a safe status below
            raise _service_failure(error) from error
    return {
        "inserted_events": outcome.inserted_events,
        "failed_source_ids": list(outcome.failed_source_ids),
    }


@router.get("/dashboard")
def dashboard(request: Request) -> dict[str, object]:
    """Observed delta totals; unknown metrics stay null rather than becoming zero."""
    container = container_for(request)
    with container.lock:
        summary = container.services.analytics.dashboard()
    return _summary_payload(summary)


@router.get("/data-quality")
def data_quality(request: Request) -> dict[str, object]:
    """The same quality and freshness view the dashboard shows."""
    container = container_for(request)
    with container.lock:
        summary = container.services.analytics.dashboard()
    return {
        "quality_counts": _quality_payload(summary),
        "source_freshness": _freshness_payload(summary),
    }


@router.get("/usage")
def usage(
    request: Request,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> dict[str, object]:
    """Path-free usage grouped by agent, recorded model, and opaque session."""
    if any(value is not None and value.utcoffset() is None for value in (start, end)):
        raise HTTPException(status_code=422, detail="Date bounds require a timezone")
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="Date range must end after it begins")
    container = container_for(request)
    with container.lock:
        return container.services.analytics.usage_breakdown(start, end)
