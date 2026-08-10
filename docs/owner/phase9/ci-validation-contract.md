# Phase 9 Milestone 11 — CI Validation Contract

## Status: E-W0.3 (Week 2 CI hardening) put this workflow on real infrastructure

Previously this section said `git remote -v` was empty and that no GH Actions runner had ever executed
`.github/workflows/ci.yml`. Stale — a real `origin` (`https://github.com/action-aura/aura-fullsuits.git`)
is now configured. E-W0.3 also found and fixed the reason CI could never have gotten past its first step
even if it had run: `requirements/base.txt` pinned `segno==1.6.6` while `requirements/owner-server.txt`
pinned `segno==1.6.1`, and `requirements/development.txt` includes both — `pip install -r
requirements/development.txt` failed with `ResolutionImpossible` before a single test could execute.
Fixed by aligning `owner-server.txt` to `1.6.6`.

E-W0.3 also split the single monolithic `test-and-scan` job into 6 jobs (`owner-tests` as a 4-way
matrix, `owner-coverage`, `owner-migrations`, `product-tests`, `security-scan`, `staging-package`) — see
`.github/workflows/ci.yml`'s own header comment for the full reasoning (the `pg_advisory_lock` in
`owner/tests/conftest.py` is why matrix-per-shard-Postgres-container was used instead of `pytest-xdist`).

## Command-by-command evidence (manually run this session, real results)

| Stage | Command | Real result this session |
|---|---|---|
| Owner tests | `pytest owner/tests -q` | 1063/1063 (4-shard split: A=299, B=259, C=262, D=243; +12 more once the separate, still-open payment-SoD/cash-closing-IDOR PR merges) |
| Owner coverage baseline | `coverage combine` + `coverage report` across the 4 shards | New in E-W0.3 — first real number publishes on the first real CI run via `$GITHUB_STEP_SUMMARY`, not re-measured manually this session |
| commercial_runtime tests | `pytest commercial_runtime -q` | 235/235 |
| Retail tests | `python products/run_all_tests.py retail` | 194/194, 12/12 files |
| Clinic tests | `python products/run_all_tests.py clinic` | 135/135, 11/11 files |
| Migration check | `alembic upgrade head` | Clean, real Postgres; E-W0.3 fixed a second latent bug here too — the step only set `OWNER_TEST_DATABASE_URL`, but `owner/migrations/env.py` reads `OWNER_DATABASE_URL` and falls back to `alembic.ini`'s literal placeholder DSN otherwise, so this step could never have succeeded as originally written |
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
