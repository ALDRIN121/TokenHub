"""Module entrypoint for the detached child and interactive command."""

from __future__ import annotations

import os
import sys

from tokenhub.cli import main
from tokenhub.server import run_server
from tokenhub.settings import TokenHubSettings

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "_serve":
        token = os.environ.pop("TOKENHUB_INTERNAL_CONTROL_TOKEN")
        run_server(TokenHubSettings(port=int(sys.argv[2])), token)
    else:
        main()
