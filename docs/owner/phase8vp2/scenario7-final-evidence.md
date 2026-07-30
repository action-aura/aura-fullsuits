# Phase 8V-P2 — Scenario 7: Final Evidence

## Tier 1: automated, real Postgres, 8 tests (all new, all passing)

`owner/tests/test_commercial_ops_renewal_requests.py` (real Postgres test database, same fixtures
and `FOR UPDATE` locking as production code — not mocked):

- `test_apply_downgrade_syncs_license_device_limit` — a real license with `device_limit=2`, two real
  active installations, a real applied renewal with `device_allowance_after=1` -> `license.device_limit
  == 1`, `resolve_effective_device_limit(lic) == 1`, both installations still counted as slot-consuming
  (untouched).
- `test_apply_downgrade_does_not_touch_installations` — the one pre-existing installation's `status`
  is re-read from the database after the downgrade and is still `ACTIVE`.
- `test_apply_downgrade_syncs_every_license_under_the_subscription` — two License rows under one
  subscription, both sync to the new value.
- `test_apply_downgrade_unchanged_limit_creates_no_audit_noise` — applying a renewal whose
  `device_allowance_after` equals the license's current `device_limit` writes zero
  `LICENSE_DEVICE_LIMIT_SYNCED` audit rows.
- `test_apply_downgrade_writes_device_limit_sync_audit_entry` — a real audit row exists with
  `before_state_redacted == {"device_limit": 2}`, `after_state_redacted == {"device_limit": 1}`.
- `test_apply_downgrade_new_activation_blocked_by_synced_limit` — after the downgrade, the license
  (re-read with a lock, exactly as `activation.py`'s real activation path does) is genuinely over its
  effective limit: `count_slot_consuming_installations(...) >= resolve_effective_device_limit(...)`,
  which is the exact boolean condition `process_activation()` uses to raise `DEVICE_LIMIT_REACHED`.

Plus two pre-existing tests reconfirmed unaffected: `test_apply_renewal_applies_plan_change_and_device_allowance`
(no license present in that test's setup -- the new sync loop finds zero rows, no regression) and
the full `test_commercial_ops_renewal_requests.py` file (24/24), full owner suite (394/394), full
`commercial_runtime` suite (219/219) -- see `final-regression-report.md`.

## Tier 2: real, live Owner dev database re-verification (not a test harness)

Run directly against the actual `aura_owner_dev` Postgres database via a real Flask app context
(`create_app('development')`), using a brand-new synthetic license/subscription/renewal (not the
Phase 8V-P session's old, already-`APPLIED` one, which cannot be re-applied):

```
SETUP: license 4081f19e-957f-4fb0-bbf8-dbb385fd9ed3 device_limit=2 subscription device_allowance=2 active installations=2
APPLIED renewal status: APPLIED
subscription.device_allowance now: 1
license.device_limit now: 1   <-- was previously NEVER synced; now synced automatically
active installations still consuming slots: 2 (both untouched)
effective device limit: 1
audit row: {'device_limit': 2} -> {'device_limit': 1}
```

Followed by the real over-limit-scan CLI command against the same live database:

```
$ flask commercial device-limit-scan --apply
{"as_of": "2026-07-30", "dry_run": false, "scanned_count": 6, "notifications_created": 1, "notifications_deduped": 1, "findings": []}
```

One new notification created for the newly-over-limit license (the old Phase 8V-P-era license stayed
deduped, as expected -- it was already notified in the prior session). No installation was
deactivated. This closes the loop the prior session left open: last time, the operator had to
manually set `device_limit` to continue testing the scan; this time the renewal alone produced the
correct enforcement value, with no manual step.

## Result: Scenario 7 is now **PASS** at the Owner/service tier (both real-Postgres-test and live-DB
tiers), for the specific gap this project tracked. Physical Android confirmation of the same
enforcement outcome remains NOT VERIFIED (no device -- see `android-physical-gate-matrix.md`), but
that was never Scenario 7's own gap; it is the same blanket Android gap every other scenario carries.
