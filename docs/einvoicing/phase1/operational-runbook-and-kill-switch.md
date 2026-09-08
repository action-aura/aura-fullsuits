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

## Default flipped to ON (2026-09-08)

`settings.py`'s `DEFAULTS['enabled']` changed from `'0'` to `'1'`: Jordan has
mandated e-invoicing since 2024-05-31, and a shop that has to find a toggle
to become compliant is not compliant. A brand-new company, or any existing
one that has never touched `POST /api/einvoicing/settings`, is now enabled
from the moment it exists — `settings.is_enabled()` returns `True` with
zero rows in `einvoice_settings`.

**"Enabled" no longer implies "submits."** `products/retail/backend/app.py`
and `products/clinic/backend/app.py` used to hard-wire `MockProvider()` as
the live provider — the one that reports `CLEARED`, with a QR and a
`provider_uuid`, without ever contacting JoFotara. Turning the DB default on
while that stayed wired in would have put a false "e-invoice cleared" claim
on a real receipt no tax authority ever saw, which is worse than the
feature being off. So the live default provider changed too, to
`providers/unconfigured.py`'s `UnconfiguredProvider` (`is_configured =
False`). `worker.py`'s `OutboxWorker.run_once()` checks that flag before
leasing or touching a single row — see the second early return, right next
to the `settings.is_enabled()` one — and holds every queued row exactly
where it is if the provider can't submit, specifically so `_apply_retry`
can never count an unconfigured install's ticks against a row's retry
budget and walk a real invoice to `FAILED_PERMANENT`.

**What an operator sees on a freshly shipped, unconfigured install:**
- Sales/invoices enqueue normally — `einvoice_outbox` fills up with
  `QUEUED` rows, gapless numbers still get allocated per the configured
  invoice family.
- Nothing ever submits. No network call happens, no QR code appears on any
  receipt, no `CLEARED` status, no clearance is ever claimed to anyone.
- `POST /api/einvoicing/outbox/run-once` returns
  `{'ran': False, 'reason': 'unconfigured'}` and leaves every row untouched
  (same status, same `attempt_count`) — this is the same shape as the
  killswitch/disabled `{'ran': False, 'reason': 'disabled'}` response, just
  a different reason.
- Rows queue indefinitely until a real provider (Phase 2's
  `DirectISTDProvider`, once ISTD portal credentials exist) is wired in to
  replace `UnconfiguredProvider` — at which point the worker can drain the
  backlog.

**Re-enabling `MockProvider` (development/test only, never a shipped
install):** set `AURA_EINVOICING_ALLOW_MOCK=1` before starting the app.
Both `app.py` files check this env var when constructing the module-level
`_einvoicing_provider` — with it unset (the shipped default), the provider
is `UnconfiguredProvider`; with it set to `'1'`, the provider is
`MockProvider()`, which genuinely completes a round trip (fake CLEARED
outcome, fake QR) so existing integration tests exercising the full
enqueue-to-clear pipeline keep working. **Never set this in production** —
it is indistinguishable, from the receipt outward, from a real clearance.

**The three disable layers are unchanged and still each independently
sufficient**, including against this new default:
1. `AURA_EINVOICING_DISABLED=1` — still build/ops hard off, no DB or file
   I/O at all.
2. `killswitch.py`'s `DISABLED` flag file — still the fastest per-install
   override, unaffected by what the DB default is.
3. An explicit per-company `enabled='0'` — still beats the new default the
   same way it always beat `'1'`; the only change is that a company must
   now be explicitly turned off to stay off, instead of explicitly turned
   on to turn on.

**Known gap, not fixed by this change:** `products/*/backend/app.py`'s
`_resume_einvoicing_workers()` and `routes.py`'s `POST /settings` handler
both decide whether to start a company's background worker thread by
comparing `was_enabled` (before) to `now_enabled` (after) a settings write,
or by querying `einvoice_settings WHERE skey='enabled' AND svalue='1'` for
an explicit row. Neither accounts for a company that is enabled purely by
the new default with no row ever written: `was_enabled` is already `True`
before any settings POST, so the enable transition never fires, and the
boot-time resume sweep's `SELECT ... WHERE svalue='1'` never matches a
company with no row at all. A company that never explicitly calls
`POST /api/einvoicing/settings` will have its documents enqueue but will
never get a recurring worker thread scheduled to drain them — not even
after a real provider is later configured — until something (a settings
write that produces a real `False`→`True` transition, or a manual
`POST /api/einvoicing/outbox/run-once` per tick) triggers it. This is a
routes.py/app.py concern, out of scope for the settings-default and
provider-safety change described above; it needs its own fix (e.g. the
boot-time sweep and the enable-transition check both switching to
`settings.is_enabled()` against a per-company list rather than a literal
`svalue='1'` row match).
