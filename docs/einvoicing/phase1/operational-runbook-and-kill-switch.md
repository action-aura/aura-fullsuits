# Operational runbook and kill switch

## Three independent disable layers

Evaluated most-forceful first in `settings.py::is_enabled()`:

1. **`AURA_EINVOICING_DISABLED=1`** — build/ops hard off. No DB or file
   I/O at all. Add to `.env.example`.
2. **`killswitch.py`'s `DISABLED` flag file** —
   `<app_data>/einvoicing/DISABLED`. Deliberately a clone of
   `scripts/sync/aura-sync.ps1`'s `.autosync/PAUSED` file: same content
   format (`reason=...`, `setAtUtc=...`, optional `expiresAtUtc=...`),
   same auto-expiry, same fail-safe direction (a malformed/unreadable file
   counts as disabled, not ignored) — one operational pattern for "stop an
   automated background thing right now" across this repo, not two.
3. **Per-company DB `enabled` setting.** The normal opt-in. Default `'0'`.

Any one of the three alone is sufficient to fully disable the feature.

## Rollback ladder (escalating)

| Level | Action | Effect |
|---|---|---|
| 1 | `POST /api/einvoicing/killswitch` (or the "Pause immediately" button in `einvoicing.html`) | Worker stops on its next tick, enqueue becomes a no-op, receipts stop showing a QR. Existing outbox rows are frozen, not deleted. Instant, no restart. |
| 2 | `POST /api/einvoicing/settings {enabled:'0'}` | Same, persistent, per company. |
| 3 | Set `AURA_EINVOICING_DISABLED=1` and restart | Feature inert regardless of DB state. |
| 4 | Restore `<app_data>/database/migration_backups/<product>-pre-migration-v1-to-v2-*.db` | Database returns to the exact pre-feature file (auto-created by `ensure_schema_version` before the migration ran). |
| 5 | Redeploy the previous build | A v2 database is readable by a v1 build unmodified — v1 code never references the `einvoice_*` tables. No downgrade migration needed. |

At levels 1–3, sales/invoices behavior, response shapes, and printed
receipt output are all byte-for-byte identical to pre-feature behavior —
this is exactly what `retail_einvoicing_regression_test.py` and
`clinic_einvoicing_regression_test.py` pin.

## Drill log

Actually executed end-to-end against a real running Retail install (a
real temp app-data directory, a real logged-in session, a real product
and sale, one real invoice sitting `QUEUED` in the outbox at drill time):

```
[DRILL] sale created, einvoice enqueued: {'invoice_ref': 'AURA_RETAIL:sale:1', 'status': 'queued'}
[DRILL] LEVEL 1 - set killswitch -> 200
[DRILL] LEVEL 1 - run-once while paused -> {'ran': False, 'reason': 'disabled'}
[DRILL] LEVEL 1 - status shows killswitch disabled=True reason=drill-level-1
[DRILL] LEVEL 1 - clear killswitch -> 200
[DRILL] LEVEL 1: PASS
[DRILL] LEVEL 2 - disabled via settings -> enabled=False
[DRILL] LEVEL 2 - run-once while disabled -> {'ran': False, 'reason': 'disabled'}
[DRILL] LEVEL 2: PASS
[DRILL] LEVEL 3 - env var override -> enabled=False (DB setting is still '1')
[DRILL] LEVEL 3 - env var cleared -> enabled=True
[DRILL] LEVEL 3: PASS
[DRILL] LEVEL 4 - pre-migration backup contains einvoice_* tables: [] (expect none)
[DRILL] LEVEL 4 - backup integrity_check: ok
[DRILL] LEVEL 4: PASS (backup exists, is intact, predates the feature)
[DRILL] ALL LEVELS COMPLETE
```

Level 1 also proved the worker genuinely stops mid-operation, not just
"reports disabled": with a real `QUEUED` row waiting, `run-once` returned
`{'ran': False}` while the killswitch was set, and the row was still
untouched afterward.

Level 5 (redeploy the previous build) was not drilled with an actual
second binary in this session — it rests on
`einvoicing_migration_test.py::test_v1_shaped_read_still_works_against_v2_file`,
which proves the underlying claim (a v1-shaped read path works unmodified
against a v2 database) without a real second executable. See
`phase1-residual-risk-register.md`.
