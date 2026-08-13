# Phase 9.5B-R2 — Owner-Wide Validation and Message Report

## Real hardcoded route/service messages found and localized

`app/commercial_ops/ui_routes.py`: 9 distinct `entity="..."` literals and
12 distinct `error="..."` literals (21 total call sites across more
occurrences), all wrapped in `gettext()` — see the exact list in
`rtl-defect-and-fix-log.md` item 2's sibling finding (M5 in the execution
plan). Verified via `grep` that zero unwrapped literal `entity=`/`error=`
strings remain in this file after the fix.

Checked, found clean (zero hardcoded literals) in all other route files:
`audit`, `catalog`, `customers`, `dashboard`, `installations`, `licensing`,
`licensing_admin`, `staff`, `subscriptions`, `system`.

## Real architectural correction: two messages reverted

`app/commercial_ops/pilot_lifecycle.py` (1) and `renewal_requests.py` (2)
had their raised-exception messages initially wrapped in `gettext()`, then
reverted after the full regression revealed `RuntimeError: Working outside
of request context` in 5 real tests (these functions are called directly
by tests/other services, not only via HTTP). See `rtl-defect-and-fix-log.md`
item 2 for the full technical account. This is recorded here because it
directly affects Milestone 5/28's "route/form/service message coverage"
claim: these 3 specific messages are **not** localized, by a considered,
tested, documented decision — not an oversight.

## No raw database constraint, exception, or traceback ever shown

Confirmed by review: every `error=` value shown in a template is either a
literal, human-written string (now translated) or `str(exc)` where `exc` is
always one of this codebase's own typed domain exceptions
(`InvalidRenewalTransitionError`, `PilotLifecycleError`, etc.), never a raw
SQLAlchemy/database exception surfaced to the UI.

## No security-detail or account-enumeration difference by locale

Confirmed: `select_locale()` never reads authentication/authorization
state, and no permission-check or account-existence-check code path reads
locale state — the two concerns are structurally independent (verified by
`git diff` showing zero overlap between files touched for translation and
files implementing auth/RBAC).
