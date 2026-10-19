"""Materialize canonical usage observations and the delta-identity index.

Revision ID: 0008_canonical_usage
Revises: 0007_multiple_usage_roots
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_canonical_usage"
down_revision: str | None = "0007_multiple_usage_roots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
    sa.Column("connector_id", sa.String(), nullable=False),
    sa.Column("provider", sa.String(), nullable=False),
    sa.Column("source_id", sa.String(), nullable=False),
    sa.Column("record_identity", sa.String(), nullable=False),
    sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    sa.Column("input_total_tokens", sa.Integer(), nullable=True),
    sa.Column("output_total_tokens", sa.Integer(), nullable=True),
    sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
    sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
    sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
    sa.Column("measurement_type", sa.String(), nullable=False),
    sa.Column("quality", sa.String(), nullable=False),
    sa.Column("parser_version", sa.String(), nullable=False),
    sa.Column("model_name", sa.String(), nullable=True),
    sa.Column("session_id", sa.String(), nullable=True),
    sa.Column("model_attribution", sa.String(), nullable=False),
    sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    """Add the read-optimized projection and the canonical probe index."""
    op.create_table(
        "usage_events_canonical",
        *_COLUMNS,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_canonical_source_id", "usage_events_canonical", ["source_id"])
    op.create_index("ix_usage_canonical_time", "usage_events_canonical", ["timestamp"])
    op.create_index(
        "ix_usage_canonical_model_time",
        "usage_events_canonical",
        ["provider", "model_name", "timestamp"],
    )
    op.create_index(
        "ix_usage_canonical_session",
        "usage_events_canonical",
        ["connector_id", "session_id"],
    )
    op.create_index(
        "ix_usage_events_delta_identity",
        "usage_events",
        ["connector_id", "record_identity"],
        sqlite_where=sa.text("measurement_type = 'delta'"),
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_delta_identity", table_name="usage_events")
    op.drop_index("ix_usage_canonical_session", table_name="usage_events_canonical")
    op.drop_index("ix_usage_canonical_model_time", table_name="usage_events_canonical")
    op.drop_index("ix_usage_canonical_time", table_name="usage_events_canonical")
    op.drop_index("ix_usage_events_canonical_source_id", table_name="usage_events_canonical")
    op.drop_table("usage_events_canonical")
