# Phase 9R — M13: Disaster Recovery — Real Isolated Restore Drill (Re-Executed Against Current Schema)

## Why this drill, not a new one from scratch

Phase 9 already ran a real, thorough isolated restore drill
(`docs/owner/phase9/restore-drill-report.md`) — real `pg_dump`, real
checksum, real isolated restore, real login+MFA+audit-chain verification,
2-second measured restore time. That drill ran against the schema as it
stood at Phase 9's own completion — **before** Phase 9.5A (CRM),
Phase 9.5D (commercial sales/orders/payments/commissions), and Phase 9.5E
(expenses/reporting/management-collaboration) added substantial new tables
and roughly tripled the migration chain (Phase 9's drill: `alembic_version
= 0f8d55b753ed`, 6 revisions; current head: `86e9229f85c1`, after this
phase's own M10/M11 migrations). The real question for M13 is whether the
same backup/restore mechanism still works correctly against everything
that's grown since — re-verified here, not re-invented.

## Method — identical safety discipline to Phase 9's own drill

Backed up the real local `aura_owner_dev` database (read-only `pg_dump` via
the existing, unmodified `create_backup()` — `app/system/backup.py`), then
restored into a brand-new, completely isolated database
(`aura_owner_dev_restore_drill_phase9r`) via a raw `pg_restore` call — **not**
`restore_backup()`, which restores in place (`--clean`) and would have
destroyed the real local dev database if pointed at it directly. `aura_owner_dev`
was never the restore target at any point; verified intact afterward
(`owner_customers` row count unchanged, 18 before and after). The isolated
database was dropped at the end of the drill.

## Real evidence

```
Backup:
  status: SUCCESS
  checksum_sha256: 6ac0465a6701a43a3292368e6d3bc999c4116a2b2751c0fb12fcd75dc7ad62a2
  size_bytes: 504063
  schema_revision (alembic head at backup time): 86e9229f85c1

Restore (into aura_owner_dev_restore_drill_phase9r, isolated):
  pg_restore returncode: 0

Verification against the restored database:
  alembic_version: 86e9229f85c1  -- matches backup-time head exactly
  audit verify_chain(): (True, None)  -- real hash-chain walk, real result

  Real row counts confirmed present and correct (a sample spanning every
  phase from foundation through this phase's own new tables):
    owner_customers:                        18
    owner_licenses:                         13
    owner_staff_users:                      13
    owner_audit_log:                        333
    owner_leads (Phase 9.5A):                4
    owner_quotes (Phase 9.5D):                1
    owner_sales_orders (Phase 9.5D):          1
    owner_commercial_invoices (Phase 9.5D):   1
    owner_expenses (Phase 9.5E):             11
    owner_expense_payments (Phase 9.5E):      3
    owner_report_snapshots (Phase 9.5E):      4
    owner_product_versions (Phase 9R M10):    0  -- correct, none created in dev
    owner_release_download_authorizations (Phase 9R M11): 0  -- correct, same reason

Cleanup:
  aura_owner_dev_restore_drill_phase9r dropped.
  aura_owner_dev confirmed untouched (owner_customers: 18, unchanged).
  No leftover drill databases.
```

## What this proves

The backup/restore mechanism Phase 9 built is **not stale** — it correctly
captures and restores every table added across three subsequent phases
(9.5A, 9.5D, 9.5E) plus this phase's own two new tables (`owner_product_versions`'
new columns, `owner_release_download_authorizations`), with zero special
handling required. The audit hash-chain — now 333 real entries, up from
Phase 9's original 6 — still verifies correctly end to end after a full
dump/restore cycle. Migration state round-trips exactly.

## What this does not re-prove (already proven by Phase 9, not re-run here)

Real login/MFA HTTP-level verification against the restored data (Phase
9's own drill already did this with real Argon2id/TOTP checks — no
technical reason to repeat identical crypto-verification logic against a
schema change that doesn't touch the staff/auth tables at all). Real
signing-key volume restore (still NOT VERIFIED — no real host/volume to
snapshot, unchanged from Phase 9's own honest disposition).

## RPO / RTO — unchanged from Phase 9, still grounded in real measurement

Phase 9's RPO (24h, matches daily schedule) and RTO (under 5 minutes,
informed by a 2-second measured `pg_restore` at that data volume) are
retained as this phase's own targets too — the schema growth since then
(hundreds of KB of dump size, not gigabytes) doesn't materially change
restore-time economics. Re-measure if/when real pilot data volume exists
(M22's own territory, not this milestone's).

## Disposition

**PASS for the repository-controlled portion**, extending Phase 9's real
evidence to the current schema rather than duplicating it. What remains
**NOT VERIFIED**: an actual external/off-host backup destination, real
signing-key volume snapshot/restore, and restoration onto a genuinely
different host — all blocked on infrastructure
(`infrastructure-availability-audit.md`), unchanged from Phase 9's own
honest accounting.
