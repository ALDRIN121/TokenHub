"""Application settings that can be isolated from host configuration."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True, init=False)
class TokenHubSettings:
    """Local TokenHub configuration with injectable filesystem locations."""

    home_directory: Path
    data_directory: Path
    host: str
    port: int

    def __init__(
        self,
        home_directory: Path | None = None,
        data_directory: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        selected_home = Path.home() if home_directory is None else Path(home_directory)
        selected_data_directory = (
            selected_home / ".tokenhub"
            if data_directory is None
            else Path(data_directory)
        )
        object.__setattr__(self, "home_directory", selected_home)
        object.__setattr__(self, "data_directory", selected_data_directory)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", port)
