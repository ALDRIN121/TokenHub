"""Dashboard queries over observed, normalized usage only."""

from tokenhub.database.repositories import UsageRepository
from tokenhub.domain.models import DashboardSummary


class AnalyticsService:
    def __init__(self, usage_repository: UsageRepository) -> None:
        self.usage_repository = usage_repository

    def dashboard(self) -> DashboardSummary:
        return self.usage_repository.dashboard_totals()
