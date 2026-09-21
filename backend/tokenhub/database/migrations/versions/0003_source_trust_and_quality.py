"""Persist source-root identity and current-generation quality.

Revision ID: 0003_source_trust_and_quality
Revises: 0002_cursor_prefix_fingerprint
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_source_trust_and_quality"
down_revision: str | None = "0002_cursor_prefix_fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add fail-closed root trust and durable malformed-record quality state."""
    op.add_column(
        "sources",
        sa.Column("approved_root_device", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("approved_root_inode", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sync_cursors",
        sa.Column(
            "source_unsupported_records",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Remove Task 6 source trust and quality metadata."""
    op.drop_column("sync_cursors", "source_unsupported_records")
    op.drop_column("sources", "approved_root_inode")
    op.drop_column("sources", "approved_root_device")
