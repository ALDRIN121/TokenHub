"""Create TokenHub's initial normalized persistence schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the metadata represented by ``tokenhub.database.models``."""
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("connector_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("path_fingerprint", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("evidence_codes", sa.String(), nullable=False),
        sa.Column("scan_supported", sa.Boolean(), nullable=False),
        sa.Column("parser_version", sa.String(), nullable=True),
        sa.Column("canonical_path", sa.String(), nullable=True),
        sa.Column("approved_root", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_table(
        "usage_events",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "record_identity", name="uq_usage_events_source_record"),
    )
    op.create_index("ix_usage_events_source_id", "usage_events", ["source_id"], unique=False)
    op.create_table(
        "sync_cursors",
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("byte_offset", sa.Integer(), nullable=False),
        sa.Column("source_mtime_ns", sa.Integer(), nullable=True),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("inserted_events", sa.Integer(), nullable=False),
        sa.Column("duplicate_events", sa.Integer(), nullable=False),
        sa.Column("partial_final_record", sa.Boolean(), nullable=False),
        sa.Column("unsupported_records", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_runs_source_id", "import_runs", ["source_id"], unique=False)


def downgrade() -> None:
    """Remove only TokenHub-owned normalized tables."""
    op.drop_index("ix_import_runs_source_id", table_name="import_runs")
    op.drop_table("import_runs")
    op.drop_table("sync_cursors")
    op.drop_index("ix_usage_events_source_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("sources")
