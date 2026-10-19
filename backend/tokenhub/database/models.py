"""SQLAlchemy metadata for TokenHub's normalized local database."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata shared by Alembic and test database setup."""


class SourceRecord(Base):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    connector_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    path_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    evidence_codes: Mapped[str] = mapped_column(String, nullable=False, default="")
    scan_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parser_version: Mapped[str | None] = mapped_column(String)
    canonical_path: Mapped[str | None] = mapped_column(String)
    approved_root: Mapped[str | None] = mapped_column(String)
    approved_root_device: Mapped[int | None] = mapped_column(Integer)
    approved_root_inode: Mapped[int | None] = mapped_column(Integer)


class _UsageEventColumns:
    """Columns shared by the raw observations and the canonical projection.

    Both tables carry the same shape so a canonical row can be read with the
    same projections as a raw row, while the projection is rebuilt from the
    ranking rule instead of being recomputed on every analytics request.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connector_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    record_identity: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_total_tokens: Mapped[int | None] = mapped_column(Integer)
    output_total_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    measurement_type: Mapped[str] = mapped_column(String, nullable=False)
    quality: Mapped[str] = mapped_column(String, nullable=False)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String)
    model_attribution: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEventRecord(_UsageEventColumns, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("source_id", "record_identity", name="uq_usage_events_source_record"),
        Index("ix_usage_events_time", "measurement_type", "timestamp"),
        Index("ix_usage_events_model_time", "provider", "model_name", "timestamp"),
        Index("ix_usage_events_message", "connector_id", "record_identity"),
        # The canonical ranking partitions by (connector_id, record_identity)
        # for delta observations only. A partial index keeps that probe an
        # index seek instead of a table scan (DBOS uses the same shape for its
        # queue and retention indexes).
        Index(
            "ix_usage_events_delta_identity",
            "connector_id",
            "record_identity",
            sqlite_where=text("measurement_type = 'delta'"),
        ),
    )


class CanonicalUsageEventRecord(_UsageEventColumns, Base):
    """Materialized canonical delta observations.

    Rebuilt from :class:`UsageEventRecord` inside the ingest transaction. The
    ranking rule stays in one place; readers only scan this table, so the
    ``row_number()`` window and the ``UNION ALL`` never run on the read path.
    """

    __tablename__ = "usage_events_canonical"
    __table_args__ = (
        Index("ix_usage_canonical_time", "timestamp"),
        Index("ix_usage_canonical_model_time", "provider", "model_name", "timestamp"),
        Index("ix_usage_canonical_session", "connector_id", "session_id"),
    )


class AutoImportRootRecord(Base):
    """Explicit consent to include future sources within one unchanged root."""

    __tablename__ = "auto_import_roots"

    connector_id: Mapped[str] = mapped_column(String, primary_key=True)
    approved_root: Mapped[str] = mapped_column(String, primary_key=True)
    approved_root_device: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_root_inode: Mapped[int] = mapped_column(Integer, nullable=False)


class SyncCursorRecord(Base):
    __tablename__ = "sync_cursors"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    byte_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    source_mtime_ns: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    prefix_fingerprint: Mapped[str | None] = mapped_column(String)
    source_unsupported_records: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )


class ImportRunRecord(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    inserted_events: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_events: Mapped[int] = mapped_column(Integer, nullable=False)
    partial_final_record: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unsupported_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
