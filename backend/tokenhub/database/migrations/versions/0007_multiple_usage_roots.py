"""Preserve separate consent for active and archived usage folders."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, DateTime

revision: str = "0007_multiple_usage_roots"
down_revision: str | None = "0006_performance_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_events", Column("reconciled_at", DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("auto_import_roots", naming_convention={"pk": "pk_%(table_name)s"}) as batch:
        batch.drop_constraint("pk_auto_import_roots", type_="primary")
        batch.create_primary_key("pk_auto_import_roots", ["connector_id", "approved_root"])


def downgrade() -> None:
    op.drop_column("usage_events", "reconciled_at")
    # Older versions support one folder per connector. Keep one existing grant
    # deterministically without ever expanding its consent to another path.
    op.execute("DELETE FROM auto_import_roots WHERE approved_root NOT IN "
               "(SELECT MIN(approved_root) FROM auto_import_roots AS roots "
               "WHERE roots.connector_id = auto_import_roots.connector_id)")
    with op.batch_alter_table("auto_import_roots") as batch:
        batch.drop_constraint("pk_auto_import_roots", type_="primary")
        batch.create_primary_key("pk_auto_import_roots", ["connector_id"])
