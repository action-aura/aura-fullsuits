# Phase 9R — Pre-Infrastructure Handover

## 1. Exact current branch

`phase9r/real-secure-remote-production`

## 2. Exact current commit

To be finalized as the closure commit after this document and the final
regression evidence are committed together (see §16, filled in below, and
the git closure commit itself). The pre-closure-doc HEAD was `b5dd17a`.

## 3. Exact starting tag and commit

`aura-owner-expenses-reporting-phase9-5e-complete` → `bd126818153de019eaf94c7a3996a3e252afdae2`

## 4. Commit list — 14 Phase 9R commits (chronological)

```
67f0889  docs(phase9r): M1 -- deployment architecture decision
959e10c  docs(phase9r): M0 -- entry gate closed with executed baseline evidence
585dc49  feat(phase9r): M2 -- fail-closed environment separation config
622c624  docs(phase9r): M3/M9/M12 -- key lifecycle, signed leases, backup policy
14805a5  feat(phase9r): M4 -- PostgreSQL production hardening
9d3abd1  feat(phase9r): M5 -- scheduler CLI wiring; reconcile M1/M4 with Phase 9
8c71126  fix(phase9r): M6 -- real request-body-limit bug found and fixed
c6b67b3  docs(phase9r): M7 -- rate-limiting audit, corrected stale docs
a1dd46f  docs(phase9r): M8 -- remote licensing API hardening audit
1b8be67  feat(phase9r): M10 -- product release authority (publish/withdraw)
2bf214b  feat(phase9r): M11 -- private authorized release distribution
4a3eaed  docs(phase9r): M12/M13 -- real isolated restore drill re-run against current schema
39eab90  fix(phase9r): M14/M15 -- redact M11's download token from logs, extend alerts
b5dd17a  docs(phase9r): M16/M17 -- deployment pipeline reconciliation, migration classification
```

(Note: commit order above is chronological by authorship; `git log` order
places most-recent first — both listings refer to the same 14 commits.)

## 5. Files changed

63 files changed, 4,700 insertions(+), 12 deletions(-) across the full
Phase 9R commit range (`bd12681..b5dd17a`).

## 6. Migrations added

| Revision | Down-revision | Purpose |
|---|---|---|
| `13944658bddf` | `f5959fdb9738` | M4 — `owner_installations` composite index on `(license_id, installation_label)` |
| `96429a63cb29` | `13944658bddf` | M10 — release-authority columns on `owner_product_versions` |
| `86e9229f85c1` | `96429a63cb29` | M11 — new `owner_release_download_authorizations` table |

Current head: `86e9229f85c1`.

## 7. Indexes added

`ix_owner_installations_license_id_installation_label` — composite index on
`owner_installations(license_id, installation_label)`, the pair every
activation/check-in/refresh request looks up by (found via direct
cross-reference of real query code against `pg_indexes`, not assumed).

## 8. Configuration changes

New `app/config.py` fields: `DB_STATEMENT_TIMEOUT_MS`,
`DB_LOCK_TIMEOUT_MS`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`, `DB_POOL_SIZE`,
`DB_MAX_OVERFLOW`, `TRUSTED_PROXY_COUNT`, `ALLOWED_HOSTS`,
`BACKUP_TARGET_URL`, `SCHEDULER_ROLE`, `STRICT_CONFIG`,
`MAX_CONTENT_LENGTH` (decoupled from `MAX_REQUEST_BYTES`),
`RELEASE_ARTIFACT_DIRECTORY`, `RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS`. Six new
fail-closed staging/production startup checks in `BaseConfig.validate()`.
Fixed one pre-existing bug in the same method (missing-secret check read
`os.environ` directly instead of the resolved class attribute).

## 9. Scheduler changes

`app/scheduling.py` (new) — `require_scheduler_owner()` gate. New CLI
command `flask reports generate-scheduled` (`app/cli.py`) — the
`REPORT_GENERATED_BY.SCHEDULER` value was structurally unreachable before
this; now wired to a real, owner-gated, idempotent periodic-generation
path.

## 10. Licensing changes

`app/api_external/routes.py`: `_bounded_payload()` (restores the licensing
API's own tighter 64KB request-size limit, independent of the raised
global `MAX_CONTENT_LENGTH`); two new routes for release-download
authorization/fetch. `app/licensing_service/reason_codes.py`: new codes
(`RELEASE_NOT_AVAILABLE`, `TOKEN_*`, `ARTIFACT_UNAVAILABLE`) integrated
into the existing anti-enumeration normalization architecture.
`app/licensing_service/ratelimit.py`: two new policies
(`release_download_authorize`, `release_download_fetch`).

## 11. Release/distribution changes

`app/releases/authority.py` (new) — `publish_release()`/`withdraw_release()`.
`app/releases/distribution.py` (new) — `authorize_download()`/
`fetch_download()`. `app/releases/storage.py` (new) — local/test artifact
storage adapter. `app/models/release_distribution.py` (new) —
`ReleaseDownloadAuthorization`. `ProductVersion` model extended with 10 new
columns (publication lifecycle). `import_release_manifest()` now creates
`DRAFT`-only rows (real behavior change, proven by test).

## 12. Backup/restore changes

No code changes — Phase 9's existing `app/system/backup.py` (`create_backup()`/
`restore_backup()`) reused unmodified. Real evidence added: a fresh
isolated restore drill executed against the current (post-9.5A/D/E)
schema, re-confirming the mechanism still works correctly across three
subsequent phases of growth.

## 13. Observability changes

`app/observability/logging_config.py`: one new redaction pattern (release-
download bearer token in URL path — real gap found and closed).
`docs/owner/phase9/alert-catalog.md` (Phase 9's real design catalog):
extended with 5 rows for this phase's new signals.

## 14. Security fixes

1. Unbounded DB statement/lock/idle-in-transaction timeouts (M4)
2. Missing `Installation` composite index (M4)
3. Dead scheduler execution path (M5)
4. Broken expense-attachment uploads over 64KB — global `MAX_CONTENT_LENGTH`
   incorrectly shared with the licensing API's own tighter bound (M6)
5. Incorrect License-status check in the new download-authorization flow
   (M11 — caught before shipping, by the tests themselves)
6. Download-token access-log leakage (M14)
7. Anti-enumeration HTTP-status side channel between two release-denial
   reasons (M11 — caught before shipping, by the tests themselves)
8. `cryptography` 48.0.1 → 50.0.0, 3 CVEs (M0, confirmed unreachable by
   this codebase's actual usage, upgraded as defense in depth)
9. Broken `alembic downgrade` for migration `96429a63cb29` — `drop_constraint(None, ...)`
   is invalid; the downgrade path had never actually been executed before
   the final regression caught it via two real test failures. Fixed with
   the real Postgres-generated constraint names, proven by actually
   running the downgrade (twice) and upgrade back (M17, found during
   closure's final regression, not during M10 itself)

## 15. Tests added

27 (M2) + 5 (M4) + 9 (M5) + 4 (M6) + 2 (M7) + 9 (M10) + 11 (M11) + 2 (M14)
= **69 new Phase 9R tests**, all real, all passing at time of writing
(see §16 for the full-suite regression these run inside of).

## 16. Final test totals

| Suite | Collected | Passed | Failed | Errors | Skipped | Duration |
|---|---|---|---|---|---|---|
| Owner (final, post migration-fix) | 1,041 | 1,041 | 0 | 0 | 0 | 2387.23s (39m47s) |
| commercial_runtime | 5 | 5 | 0 | 0 | 0 | 0.58s |
| licensing_contracts | 230 | 230 | 0 | 0 | 0 | 21.00s |
| Retail (forward order) | 194 | 194 | 0 | 0 | 0 | ~9 min (12 files) |
| Retail (fully reversed order) | 194 | 194 | 0 | 0 | 0 | ~9 min (12 files), identical result |
| Clinic | 135 | 135 | 0 | 0 | 0 | ~7 min (11 files) |
| **Combined (Owner+commercial_runtime+licensing_contracts+Retail+Clinic)** | **1,605** | **1,605** | **0** | **0** | **0** | — |

Owner's first run (pre-fix) showed 2 failures
(`test_database.py::test_migration_runs_clean_from_empty_database_and_rolls_back`,
`test_phase6_migration.py::test_upgrade_on_populated_phase5_database_loses_no_records`)
— a real bug in migration `96429a63cb29`'s `downgrade()`, fixed (see §14
item 9), and the complete suite re-run from the fixed state per the
explicit "rerun the affected complete suite" instruction. The number above
is the clean, final, post-fix result — not the historical 972 baseline
forced to match; the real collected count grew to 1,041 from this phase's
own 69 new tests plus whatever else changed in the interim.

Full detail, including the pre-fix failure evidence and the real downgrade/
upgrade proof: `final-regression-report.md`.

## 17. Local PASS capabilities

Environment separation, secret/key lifecycle (validated), PostgreSQL
hardening, scheduler ownership, request-body limits, rate limiting (both
mechanisms), remote licensing authority (code-level), signed leases
(code-level), release authority, private distribution (local/test
adapter), local backup/restore (real, drilled), local observability (real,
50 health checks), local security detection, deployment pipeline
(implementation), migration/rollback tooling.

## 18. External NOT VERIFIED capabilities

Public DNS/TLS, remote licensing behavior, remote lease refresh, external
object storage, off-host backup, remote DR, remote monitoring/alert
delivery, hosted CI execution, remote staging deployment, M18-M28 in full.

## 19. Required infrastructure

See `infrastructure-acquisition-manifest.md` in full.

## 20. Required credentials

Real `OWNER_SECRET_KEY`, `OWNER_LICENSE_PEPPER`, database credentials,
object-storage credentials, deployment SSH/API credential, monitoring
webhook credential — none exist yet, none are named with values anywhere
in this repository.

## 21. Required provider decisions

Remote host provider/region, DNS registrar (if domain not already owned),
object-storage provider, monitoring/alerting provider — all the owner's
decision, informed by `production-cost-model.md`'s real 2026 pricing
examples.

## 22. First remote deployment steps

`REMOTE-RESUMPTION-RUNBOOK.md` steps 6-13.

## 23. Rollback steps

`deployment-pipeline-and-migrations.md`'s M17 section; Phase 9's
`disaster-recovery-runbook.md` for the full host-loss/corruption/signing-
key-loss scenarios.

## 24. First DNS steps

`REMOTE-RESUMPTION-RUNBOOK.md` step 8; exact record requirements in
`infrastructure-acquisition-manifest.md`'s "Domain and DNS" section.

## 25. First TLS steps

`REMOTE-RESUMPTION-RUNBOOK.md` step 14 — Caddy's automatic ACME issuance
against the real domain once DNS resolves; do not accept `tls internal` as
equivalent.

## 26. First PostgreSQL steps

`REMOTE-RESUMPTION-RUNBOOK.md` step 10; real least-privilege role creation
per `production-postgresql.md`'s explicit finding (do not reuse a
superuser-adjacent role from local dev).

## 27. First object-storage steps

`REMOTE-RESUMPTION-RUNBOOK.md` step 11; implement the real-storage
counterpart to `app/releases/storage.py`'s local/test adapter (same
function signatures) — deliberately not built yet.

## 28. First monitoring steps

`REMOTE-RESUMPTION-RUNBOOK.md` step 12; wire the real destination against
`alert-catalog.md`'s already-mapped signals — no new signal design needed.

## 29. First remote browser suite

`REMOTE-RESUMPTION-RUNBOOK.md` step 17 (M19) — real Chromium against the
real HTTPS domain, full checklist in the original Phase 9R scope.

## 30. First remote licensing suite

`REMOTE-RESUMPTION-RUNBOOK.md` step 18 (M20) — real synthetic clients,
never simulated via direct `Installation` row insertion.

## 31. Controlled-pilot prerequisites

Full M18-M27 PASS, zero P0/P1, per `phase9r-final-decision.md`'s
"Controlled-pilot readiness: BLOCKED_BY_INFRASTRUCTURE" section.

## 32. Prohibited shortcuts

Do not fake remote evidence. Do not simulate activation via direct
database inserts. Do not accept a self-signed/internal-CA certificate as
"trusted HTTPS." Do not create either final tag before real PASS. Do not
push to the remote without the repository owner's explicit authorization.
Do not skip the isolated-restore re-verification if the schema has grown
further since this closure. Do not reuse any local/dev/staging secret in
production.

## 33. Final-tag conditions

`aura-owner-real-production-phase9r-complete` may be created **only**
after every PASS requirement in the original Phase 9R scope is met with
real evidence, per `REMOTE-RESUMPTION-RUNBOOK.md` step 27's own explicit
condition. Not speculative, not time-pressured.
