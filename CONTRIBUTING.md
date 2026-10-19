# Contributing to TokenHub

Contributions through issues and pull requests are welcome. Fork the repository
and open a pull request from a focused branch. You do not need collaborator
access to contribute. The maintainer, [@ALDRIN121](https://github.com/ALDRIN121),
decides what is merged and merges changes after the required checks pass.

## Before starting

- Check existing issues and pull requests to avoid duplicate work.
- Open a feature request before a substantial change to the data model, supported
  sources, collection behavior, or dashboard navigation.
- Report security vulnerabilities privately using [SECURITY.md](SECURITY.md).
- Follow the [code of conduct](CODE_OF_CONDUCT.md).

## Development setup

Use Python 3.12 or newer, Node.js 22, and uv.

```bash
uv sync --all-groups
npm --prefix frontend ci
npm --prefix frontend run build
uv run tokenhub --no-open
```

The dashboard is bundled under `backend/tokenhub/web`. For frontend changes,
rebuild it and include the resulting bundled files in the same pull request.

## Validate your change

Run the checks relevant to your change. CI runs these checks across Linux,
macOS, and Windows and verifies that the bundled dashboard matches its source.

```bash
uv run --frozen pytest -q
uv run --frozen pytest tests/integration -m integration -q
uv run --frozen ruff check backend tests scripts
uv run --frozen mypy backend
npm --prefix frontend run test -- --run
npm --prefix frontend run build
uv build
python scripts/verify_distribution.py dist/*
```

Use synthetic data and temporary directories for tests. Tests must not inspect
real provider accounts, credentials, local session histories, or a developer's
home directory. Avoid provider network calls and provider subprocesses.

## Pull requests

Explain the problem, resulting behavior, and validation. Include before/after
screenshots for visible dashboard changes, using generated sample data. Keep
unrelated refactors and generated local artifacts out of the pull request.

Source connectors must retain only allowlisted usage metadata, preserve the
explicit source-approval boundary, and expose unavailable counters rather than
inventing zero values or estimates. Include a focused regression test for a
behavioral fix when appropriate.

The `main` branch requires a pull request, the three platform verification
checks, and resolved review conversations. Coding apps and contributors can
propose changes on branches; only the maintainer's account can merge to `main`.
A CODEOWNERS entry requests maintainer review; it does not itself grant merge
permission or require another person to approve the sole maintainer's own PR.

Do not attach real session logs, prompts, transcripts, provider databases,
credentials, access tokens, or private filesystem paths to issues or PRs. Use a
small synthetic reproduction and redact diagnostics.
