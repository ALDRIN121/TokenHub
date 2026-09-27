"""Retain model and session metadata for usage attribution."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_usage_metadata"
down_revision: str | None = "0004_auto_import_roots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("model_name", sa.String(), nullable=True))
    op.add_column("usage_events", sa.Column("session_id", sa.String(), nullable=True))
    op.add_column("usage_events", sa.Column("model_attribution", sa.String(), nullable=False, server_default="unknown"))


def downgrade() -> None:
    with op.batch_alter_table("usage_events") as batch:
        batch.drop_column("model_attribution")
        batch.drop_column("session_id")
        batch.drop_column("model_name")
