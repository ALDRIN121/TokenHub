"""Ordered connector registry with failure isolation."""

from collections.abc import Sequence

from tokenhub.connectors.antigravity.connector import AntigravityConnector
from tokenhub.connectors.claude.connector import ClaudeConnector
from tokenhub.connectors.codex.connector import CodexConnector
from tokenhub.connectors.copilot.connector import CopilotConnector
from tokenhub.connectors.hermes.connector import HermesConnector
from tokenhub.connectors.protocol import (
    DetectionResult,
    DiscoveryContext,
    UsageConnector,
)
from tokenhub.discovery.locations import AgentLayout
from tokenhub.domain.models import Provider, SourceState
from tokenhub.ingestion.progress import ScanInterrupted, report


class ConnectorRegistry:
    def __init__(self, connectors: Sequence[UsageConnector]) -> None:
        self.connectors = tuple(connectors)

    @classmethod
    def default(cls) -> "ConnectorRegistry":
        return cls([ClaudeConnector(), CodexConnector(), HermesConnector(), CopilotConnector(), AntigravityConnector()])

    def layouts(self) -> tuple[AgentLayout, ...]:
        """Every agent's location layout, for host probes and the location report.

        ``UsageConnector`` declares ``layout`` so a real connector cannot omit
        it; the lookup stays tolerant because embedders and tests register
        duck-typed connectors that only implement the four methods.
        """
        return tuple(
            layout
            for connector in self.connectors
            if (layout := getattr(connector, "layout", None)) is not None
        )

    def discover_all(self, context: DiscoveryContext) -> list[DetectionResult]:
        results: list[DetectionResult] = []
        for connector in self.connectors:
            report()
            try:
                results.append(connector.detect(context))
            except ScanInterrupted:
                raise
            except Exception:  # noqa: BLE001 - isolate arbitrary connector failures
                provider = getattr(connector, "provider", None)
                results.append(
                    DetectionResult(
                        connector_id=connector.connector_id,
                        display_name=connector.display_name,
                        provider=provider if isinstance(provider, Provider) else None,
                        state=SourceState.ERROR,
                        evidence_codes=("discovery_error",),
                    )
                )
        return results
