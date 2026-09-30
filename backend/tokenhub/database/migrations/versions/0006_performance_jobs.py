"""Indexes for bounded aggregate queries."""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_performance_jobs"
down_revision: str | None = "0005_usage_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_usage_events_time", "usage_events", ["measurement_type", "timestamp"])
    op.create_index("ix_usage_events_model_time", "usage_events", ["provider", "model_name", "timestamp"])
    op.create_index("ix_usage_events_message", "usage_events", ["connector_id", "record_identity"])


def downgrade() -> None:
    for name in ("ix_usage_events_message", "ix_usage_events_model_time", "ix_usage_events_time"):
        op.drop_index(name, table_name="usage_events")
