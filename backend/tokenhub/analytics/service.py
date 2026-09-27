"""Dashboard queries over observed, normalized usage only."""

from typing import Any

from tokenhub.analytics.breakdown import usage_breakdown
from tokenhub.database.repositories import UsageRepository
from tokenhub.domain.models import DashboardSummary


class AnalyticsService:
    def __init__(self, usage_repository: UsageRepository) -> None:
        self.usage_repository = usage_repository

    def dashboard(self) -> DashboardSummary:
        return self.usage_repository.dashboard_totals()

    def usage_breakdown(self) -> dict[str, Any]:
        return usage_breakdown(self.usage_repository.observed_events())
