"""Module entrypoint for the detached child and interactive command."""

from __future__ import annotations

from tokenhub.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
