"""SQLite engine creation for TokenHub-owned databases only."""

import hashlib
import logging
import re
from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event

from tokenhub.analytics.calendar import sqlite_bucket
from tokenhub.database.models import Base
from tokenhub.settings import TokenHubSettings

logger = logging.getLogger(__name__)

# PocketBase's shipped SQLite configuration (core/db_connect.go in v0.40.5),
# minus its Go-driver-only `_defensive` flag. busy_timeout is applied first so
# a connection blocks on a busy database before WAL mode is negotiated.
_SQLITE_PRAGMAS = (
    "PRAGMA busy_timeout=10000",
    "PRAGMA journal_mode=WAL",
    "PRAGMA journal_size_limit=200000000",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32000",
    "PRAGMA mmap_size=268435456",
)


def _natural_order(left: str, right: str) -> int:
    def key(value: str) -> list[str | int]:
        return [int(part) if part.isdecimal() else part.casefold()
                for part in re.split(r"(\d+)", value)]
    first, second = key(left), key(right)
    return (first > second) - (first < second)


def create_engine_for(settings: TokenHubSettings) -> Engine:
    """Create the sole application database under TokenHub's data directory."""
    database_path = settings.data_directory / "tokenhub.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        # The engine is created during ASGI startup, while sync routes run in a
        # worker threadpool, so a pooled connection can legitimately be used
        # from a different thread than the one that opened it.
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection: Connection, _: object) -> None:
        dbapi_connection.create_collation("TOKENHUB_NATURAL", _natural_order)
        dbapi_connection.create_function("tokenhub_trend_bucket", 4, sqlite_bucket, deterministic=True)
        dbapi_connection.create_function(
            "tokenhub_session_key", 3,
            lambda connector, session, source: hashlib.sha256(
                f"{connector}\0{session or source}".encode()
            ).hexdigest()[:24], deterministic=True,
        )
        # Explicit BEGIN makes multi-query read views one WAL snapshot.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        for pragma in _SQLITE_PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin_snapshot(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    return engine


def optimize_database(engine: Engine) -> None:
    """Refresh planner statistics and bound the WAL after a write burst.

    PocketBase runs the same pair from its nightly ``__pbDBOptimize__`` cron;
    a local app can do it after every ingest because there is no concurrent
    writer to starve. Both statements run outside SQLAlchemy's snapshot
    transaction, on a pooled DBAPI connection in autocommit mode.
    """
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute("PRAGMA optimize")
            try:
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # noqa: BLE001 — a reader may hold the WAL
                logger.debug("WAL checkpoint skipped; a reader still holds a snapshot")
        finally:
            cursor.close()
    finally:
        raw.close()


def initialize_database(engine: Engine) -> None:
    """Create application metadata for isolated tests only."""
    Base.metadata.create_all(engine)
