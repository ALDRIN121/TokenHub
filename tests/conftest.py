from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from tokenhub.database.session import create_engine_for, initialize_database
from tokenhub.settings import TokenHubSettings

from tests.service_support import Services, services_for, token_record


@pytest.fixture
def app_services(tmp_path: Path) -> Iterator[Services]:
    home = tmp_path / "home"
    session_file = home / ".codex" / "sessions" / "synthetic.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_bytes(
        token_record(
            1,
            input_tokens=100,
            output_tokens=25,
            cached_input_tokens=70,
            cache_write_input_tokens=10,
            reasoning_output_tokens=20,
        )
    )
    (home / ".claude").mkdir()
    (home / ".hermes").mkdir()
    (home / ".hermes" / "state.db").write_bytes(b"synthetic unsupported database")
    engine = create_engine_for(
        TokenHubSettings(home_directory=home, data_directory=tmp_path / "data")
    )
    initialize_database(engine)
    with Session(engine) as session:
        yield services_for(session, home, session_file)
    engine.dispose()
