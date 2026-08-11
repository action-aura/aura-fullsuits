# Phase 9.5B Milestone 21 — Employee Preflight Report

Extended (not replaced) `owner/app/commercial_ops/preflight.py` — `_check_employee_domain_integrity()`,
wired into the existing `run_preflight()` alongside the pre-existing checks. Read-only, side-effect-free,
same discipline as every other check in this module.

## Checks added

- **`no_duplicate_employee_numbers`** / **`no_orphan_employee_profiles`** — defense-in-depth: both are
  already structurally guaranteed by real DB constraints (`UNIQUE employee_number`, FK `staff_user_id`
  with no cascade), so these should always report `OK`; a `FAIL` here would mean a constraint was somehow
  bypassed (e.g. a raw SQL migration mistake) — exactly the "catch it before a real call fails" spirit
  this module exists for.
- **`at_least_one_usable_super_admin`** — the real, literal Non-Negotiable Principle 11 check.
- **`presence_thresholds_valid`** — sanity on `ONLINE_THRESHOLD_SECONDS < RECENTLY_ACTIVE_THRESHOLD_SECONDS`.

## Real bug found and fixed while adding this check

First version FAILed preflight on any freshly-migrated, staff-less test database (3 pre-existing
`test_commercial_ops_preflight.py` tests broke — `result.ok` flipped `True → False`). Investigated: a
completely fresh/pre-bootstrap Owner install legitimately has **zero** `StaffUser` rows before the first
`flask create-superadmin` ever runs — this is expected, not a misconfiguration, matching the exact
non-blocking precedent `_check_super_admin_mfa()` already set for "synthetic/dev setup." **Fixed**: the
check now distinguishes "no `StaffUser` rows at all" (→ `WARNING`, not bootstrapped yet) from "`StaffUser`
rows exist but none is a usable Super Admin" (→ real `FAIL`, a genuine lockout). All 15 preflight tests
(3 pre-existing + 12 covering both the original module and this phase's new checks) pass after the fix.

## No secrets printed

Every check's `detail` string contains only counts, employee numbers (not secret), and status enums —
confirmed no password/token/MFA-secret value is ever interpolated into a `PreflightCheck.detail`.
