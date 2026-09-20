"""SQLite engine creation for TokenHub-owned databases only."""

from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event

from tokenhub.database.models import Base
from tokenhub.settings import TokenHubSettings


def create_engine_for(settings: TokenHubSettings) -> Engine:
    """Create the sole application database under TokenHub's data directory."""
    database_path = settings.data_directory / "tokenhub.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection: Connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine: Engine) -> None:
    """Create application metadata for isolated tests only."""
    Base.metadata.create_all(engine)
