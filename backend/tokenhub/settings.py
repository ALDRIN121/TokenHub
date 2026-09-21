"""Application settings that can be isolated from host configuration."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7432

# TokenHub is a local-first observatory: it binds to loopback only. Wildcard
# binds (0.0.0.0, ::) and host-header style values are refused at construction
# so a bad host can never reach uvicorn.
_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MIN_PORT = 1
_MAX_PORT = 65535


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
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        """Bind loopback only and resolve TokenHub-controlled paths."""
        if host not in _LOOPBACK_BIND_HOSTS:
            raise ValueError("TokenHub binds to loopback hosts only")
        if not _MIN_PORT <= port <= _MAX_PORT:
            raise ValueError("port must be within the TCP port range")
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
