# Security policy

## Supported versions

Security fixes target the latest stable TokenHub release. Update to the latest
release before reporting a problem that may already have been fixed.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/ALDRIN121/TokenHub/security/advisories/new).
Include the affected version, operating system, affected component, impact, and
a minimal reproduction using synthetic data. The maintainer will review the
report and coordinate any fix and disclosure through the private advisory.

Do not open a public issue containing an exploitable vulnerability or sensitive
data. Do not upload provider session histories, chat transcripts, auth files,
API keys, access tokens, raw provider databases, or private paths. Redact
necessary diagnostics and use generated fixtures wherever possible.

For ordinary bugs and feature requests, use the public issue templates.

## Security boundaries

TokenHub runs on the local machine and requires explicit source approval before
importing usage. It retains allowlisted usage metadata rather than transcript
content, and does not call provider APIs or launch provider executables.

Reports involving source-approval bypass, access outside an approved source,
credential access, unintended data retention, remote access to the local
service, or compromised package/release delivery are especially relevant.

## Contribution and release controls

The maintainer controls merges to `main`; required platform CI checks must pass.
Production PyPI deployment requires the maintainer's approval and is restricted
to `main`. Release workflows use trusted publishing and reviewed artifacts.
These controls complement, rather than replace, review of proposed changes.
