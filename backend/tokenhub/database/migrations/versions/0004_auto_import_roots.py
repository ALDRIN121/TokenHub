"""Persist explicit consent for automatic imports from a session root."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_auto_import_roots"
down_revision: str | None = "0003_source_trust_and_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auto_import_roots",
        sa.Column("connector_id", sa.String(), primary_key=True),
        sa.Column("approved_root", sa.String(), nullable=False),
        sa.Column("approved_root_device", sa.Integer(), nullable=False),
        sa.Column("approved_root_inode", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auto_import_roots")
