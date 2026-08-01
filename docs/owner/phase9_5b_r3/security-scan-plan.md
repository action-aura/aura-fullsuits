# Phase 9.5B-R3 — Security Scan Plan

## Dependency scan

Real tool: `pip-audit` (already used and documented in this project's
history — Phase 9's own dependency remediation used it). Run against the
resolved environment for Owner (`requirements/owner-server.txt` +
`requirements/development.txt`), and separately check
`commercial_runtime`/Retail/Clinic requirement sources for a distinct
resolved set where they differ. Record scanner version, command, findings,
disposition.

## Secret scan

Real tool: `detect-secrets` (pinned, dev-only, not added to production
requirements) if available in the environment; otherwise install into the
existing `.venv` for this session's use only (a dev-tooling install, not a
runtime dependency change) and document that choice. Scan tracked files,
current diff, and files touched this wave and last wave (templates, JS,
catalogs, docs, tests, config).

## Execution discipline

Both scans are **executed**, not asserted from "no dependency changed."
Findings are triaged individually; any P0/P1 is fixed before this wave can
PASS; any false positive is documented with an exact justification, not
silenced via a broad allowlist.
