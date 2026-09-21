"""Add a consumed-prefix fingerprint to incremental scan cursors.

Revision ID: 0002_cursor_prefix_fingerprint
Revises: 0001_initial
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_cursor_prefix_fingerprint"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow safe validation of the bytes consumed by an existing cursor."""
    op.add_column(
        "sync_cursors",
        sa.Column("prefix_fingerprint", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove only the optional cursor validation metadata."""
    op.drop_column("sync_cursors", "prefix_fingerprint")
