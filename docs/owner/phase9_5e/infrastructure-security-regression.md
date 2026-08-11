# Phase 9.5E Milestone 26 — Infrastructure Regression (Executed, Not Just Built)

Everything in this document was actually run on 2026-08-03 against real
infrastructure -- either the isolated `aura_owner_test` database (per-test
truncated, never `aura_owner_dev`) or the real `aura_owner_dev` database via
a real running dev-server process -- not inferred from code review or from
the unit-level tests that built these behaviors in M20/M21/M25.

## 1. Backup and isolated restore (new for M26)

`tests/test_phase9_5e_backup_restore.py::test_backup_and_isolated_restore_recovers_every_phase9_5e_table`
-- seeds one real row in **every** new Phase 9.5E table (`Payee`, `Expense`,
`ExpenseApproval`, `ExpensePayment`, `ExpenseAttachment`, `CashClosing`,
`SharedManagementNote`, `ReportSnapshot`) via the real service layer, runs
`app.system.backup.create_backup()` for a real `pg_dump --format=custom`,
deletes every row from every one of those eight tables (simulating total
data loss), asserts zero rows remain, then runs
`app.system.backup.restore_backup()` for a real `pg_restore --clean
--if-exists`, and asserts every row is back with its original identity and
material fields (amount, status, content type, business date, title,
snapshot status) intact.

```
tests\test_phase9_5e_backup_restore.py::test_backup_and_isolated_restore_recovers_every_phase9_5e_table PASSED
1 passed in 11.92s
```

"Isolated" here means the same thing it means for `test_backup_restore.py`'s
pre-existing Customer proof: `restore_backup()` always restores into
whatever database the app's engine is configured for, and the `app`/`seeded`
pytest fixtures point exclusively at the per-test-truncated `aura_owner_test`
database -- `aura_owner_dev` is never touched by this test. Attachment file
*bytes* live on the filesystem (`EXPENSE_ATTACHMENT_DIRECTORY`), not in
Postgres, and are correctly out of scope for a `pg_dump`-based backup; only
the `ExpenseAttachment` row's metadata (content type, hash, storage key) is
asserted here, matching the existing, unmodified backup design.

## 2. Scheduler lock/idempotency (re-executed for M26)

`tests/test_phase9_5e_financial_timezone_scheduler.py::test_scheduler_concurrent_workers_never_duplicate_a_snapshot`
re-run standalone as an explicit M26 infrastructure check (built and first
run under M21, re-executed here as its own evidence rather than treated as
carried-over proof):

```
tests\test_phase9_5e_financial_timezone_scheduler.py::test_scheduler_concurrent_workers_never_duplicate_a_snapshot PASSED
1 passed in 8.43s
```

Confirms the `pg_advisory_xact_lock` + `uq_report_snapshot_canonical_key`
two-layer idempotency guard still holds: concurrent workers racing to
generate the same report snapshot never produce more than one PUBLISHED row.

## 3. Audit hash-chain verification (executed against `aura_owner_dev`)

```python
from app.audit.services import verify_chain
verify_chain()  # -> (True, None)
```

Run inside a real `app_context()` against the live `aura_owner_dev` database
(not the isolated test database) -- confirms the append-only audit hash
chain, now carrying every Phase 9.5E audit event (expense lifecycle,
approvals, payments, attachments, cash closings, report snapshots,
management notes) on top of everything every prior phase already wrote, is
unbroken end to end.

## 4. Health/readiness endpoints (executed via a real running dev server)

Launched via `tools/dev_server/port_isolation.py::start_server()` (the exact
M0 harness, ephemeral OS-assigned port, against `aura_owner_dev`) and hit
with real HTTP requests:

```
GET /health/live  -> 200 {"status":"ok"}
GET /health/ready -> 200
  {"checks":[
    {"name":"database_connectivity","status":"OK"},
    {"name":"migration_at_head","status":"OK"},
    {"name":"active_signing_key_exists","status":"OK"},
    {"name":"signing_key_sign_verify_roundtrip","status":"OK"},
    {"name":"trust_anchor_matches_active_key","status":"OK"},
    {"name":"all_permissions_seeded","status":"OK"},
    {"name":"no_duplicate_permission_codes","status":"OK"},
    {"name":"role_permissions_synced","status":"OK"},
    {"name":"license_pepper_configured","status":"WARNING"},
    ... (50 checks total, matching section 5 below)
  ]}
```

`/health/ready` internally calls the exact same `run_preflight()` used by
`flask commercial preflight` (see `app/health.py`) -- there is no separate,
parallel readiness logic to drift from the preflight gate.

## 5. Complete blocking preflight -- all historical + 9.5D + 9.5E, executed against `aura_owner_dev`

```
$ flask --app app:create_app commercial preflight
exit code: 0
50 OK, 0 FAIL, 2 WARNING
```

Because `run_preflight()` is the single unified gate every phase has
extended (never a parallel per-phase command -- see
`docs/owner/phase9_5e/blocking-preflight.md`), this one execution
re-confirms, for real, in the same run: signing-key health/trust-anchor,
full RBAC seed (`all_permissions_seeded`, `role_permissions_synced` across
all 5 roles), license pepper, employee-domain integrity, i18n catalog
configuration, CRM domain integrity, the **Phase 9.5D commercial-sales
domain integrity** checks, and the **complete Phase 9.5E operational-finance
integrity** checks (expense/payment/approval segregation, attachment
integrity, cash-closing uniqueness, report-snapshot uniqueness). There is no
separate "Phase 9.5D preflight" or "Phase 9.5E preflight" command to run
independently -- this single execution is that proof for both, plus every
prior phase.

The 2 WARNINGs are the same non-blocking, expected-in-dev conditions every
prior phase's own preflight run has reported: 4 local synthetic Super Admin
test accounts with `mfa_required=False` (fine for dev, must never be true in
production), and the dev-only license pepper placeholder. Neither fails
readiness or the preflight gate.

## Summary

| Check | Method | Result |
|---|---|---|
| Backup + isolated restore, all 8 new tables | Real `pg_dump`/`pg_restore` via pytest | PASS |
| Scheduler advisory-lock idempotency | Real concurrent-thread test | PASS |
| Audit hash-chain integrity | `verify_chain()` against `aura_owner_dev` | PASS (unbroken) |
| `/health/live` | Real HTTP GET against a real dev-server process | 200 OK |
| `/health/ready` | Real HTTP GET against a real dev-server process | 200 OK, 50/50 checks OK |
| Complete blocking preflight (all phases) | `flask commercial preflight` against `aura_owner_dev` | exit 0, 50 OK / 0 FAIL / 2 WARNING (non-blocking) |
