"""SQLite engine creation for TokenHub-owned databases only."""

import hashlib
import re
from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event

from tokenhub.database.models import Base
from tokenhub.settings import TokenHubSettings


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
        dbapi_connection.create_function(
            "tokenhub_session_key", 3,
            lambda connector, session, source: hashlib.sha256(
                f"{connector}\0{session or source}".encode()
            ).hexdigest()[:24], deterministic=True,
        )
        # Explicit BEGIN makes multi-query read views one WAL snapshot.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin_snapshot(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    return engine


def initialize_database(engine: Engine) -> None:
    """Create application metadata for isolated tests only."""
    Base.metadata.create_all(engine)
