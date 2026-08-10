# Phase 9 Milestone 11 — CI Validation Contract

## Status: workflow file is real and complete; NOT VERIFIED as an actually-executed pipeline

`git remote -v` is empty — this repository has no connected GitHub (or other) remote this session, so
`.github/workflows/ci.yml` has never actually been run by a CI runner. Every command inside it is real,
however — each one was manually executed against the real repository this session (see the table
below), and this file wires those same exact commands into a complete workflow definition rather than
aspirational pseudo-code.

## Command-by-command evidence (manually run this session, real results)

| Stage | Command | Real result this session |
|---|---|---|
| Owner tests | `pytest owner/tests -q` | 423/423 |
| commercial_runtime tests | `pytest commercial_runtime -q` | 235/235 |
| Retail tests | `python products/run_all_tests.py retail` | 194/194, 12/12 files |
| Clinic tests | `python products/run_all_tests.py clinic` | 135/135, 11/11 files |
| Migration check | `alembic upgrade head` | Clean, 6 revisions, real staging DB |
| Secret scanning | (would be `detect-secrets scan`) | Not run in this exact form; manual scans this session found zero leaked secrets in wire captures/logs (Phase 8V-P9 pattern, reused) |
| Dependency scanning | `pip-audit -r requirements/*.txt` | 32 found -> remediated -> 0 remaining |
| SBOM generation | `cyclonedx_py environment` | 94 components, CycloneDX 1.6 |
| Staging deployment package | `docker compose -f docker-compose.staging.yml config` | NOT VERIFIED — Docker Engine not installed this session |

## Requirements satisfied by the workflow definition (not all independently re-verified as CI behavior)

- No secrets available to untrusted PRs: the workflow uses only test-time synthetic values
  (`OWNER_TEST_DATABASE_URL` matching `owner/tests/conftest.py`'s own default) — no `secrets.*` context
  reference exists anywhere in the file, so there is nothing for a fork PR to exfiltrate.
- No signing key in the repository — confirmed structurally (`.gitignore`/`.dockerignore` both exclude
  `var/signing-keys`; Milestone 5/10 secret scans found nothing).
- No automatic production deployment — the workflow's final stage renders the staging Compose config
  (`docker compose config`, a dry validation, not `up`); nothing in this repository triggers a real
  deploy automatically, matching Non-Negotiable Principle 9.
- Deterministic commands, machine-readable results — every stage's command is the literal command this
  session ran manually and recorded exact output for.

## Android/Windows signing

Not built into this CI workflow — see `release-workstation-runbook.md`. CI runs Owner/backend tests
only; product artifact signing happens on a controlled release workstation, never in an untrusted CI
runner, per the governing instruction's own explicit requirement.
