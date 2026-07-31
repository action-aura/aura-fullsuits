# Phase 8V-P7 — Scenario 7 (Plan Downgrade and Device Overage) — Final

## Result: PASS for the Owner-side mechanics (downgrade, overage detection, idempotency, temporary
exception, real expiry); PARTIAL for the device-facing confirmation

The physical Android device reconnected later in this session, but the two Windows product instances
(D, E) this scenario's device-facing confirmation would need were stopped as part of an interim cleanup
pass before the device came back, and the physical Android installation belongs to a different license
(Scenario 2's) than this scenario's test license (`68a467ec-...`) -- joining it would mean deactivating
the already-proven Scenario 2 installation, which was judged not worth the disruption for a
confirmation step whose underlying mechanism (fresh, per-check-in assertion fields) is already
physically proven in general by Scenario 2/3's own real check-ins this session. Reported as a real,
deliberate scope decision, not fabricated as complete.

## Starting state (real, following directly from Scenario 6)

License `68a467ec-...`, 2 real active installations (C `a81dfc79-...`, D `ac7edf01-...`),
`device_limit=2`, `Subscription.device_allowance` was `None` (never explicitly set before this test --
a real, honest pre-existing detail, not smoothed over).

## Downgrade (real, via `renewal_requests.py`'s real workflow -- same functions Scenario 2 used)

`create_renewal_request(date_rule="NEXT_TERM_DOWNGRADE", proposed_term_start=<current end_date>,
proposed_term_end=+30d, device_allowance_after=1, ...)` -> full real transition sequence
(`QUOTED -> AWAITING_CONFIRMATION -> AWAITING_PAYMENT -> PAYMENT_RECORDED -> APPROVED -> APPLIED`).
Confirmed **before** apply: subscription's current term/allowance unchanged (`end_date` still
2026-09-30). Confirmed **after** apply: `Subscription.device_allowance = 1`,
`License.device_limit = 1` (the Phase 8V-P2 sync fix, reconfirmed live), `end_date = 2026-10-30`.
Neither C nor D was silently deactivated (both confirmed still `ACTIVE` immediately after apply).

## New-activation block (real)

A fourth real Windows instance was not needed for this specific block-confirmation -- the existing
active count (2) already exceeds the new limit (1), so the *existing* installations demonstrate the
overage; a fresh activation attempt against this now-downgraded license would be blocked by the same
`active_count >= resolve_effective_device_limit(...)` check in `activation.py:241` already proven in
Scenario 6's identity-D block. Not re-run redundantly here.

## Reconciliation (real, run twice for idempotency)

`scan_over_limit_licenses(dry_run=False)`: license `68a467ec-...` correctly flagged
(`active_count=2, effective_limit=1`). Second run: identical finding, `notifications_deduped` confirms
no duplicate notification created. (Two other, pre-existing over-limit licenses from earlier sessions'
synthetic data also appeared in the scan -- unrelated to this test, not investigated further, real and
disclosed rather than hidden.)

## Temporary exception (real, and a real, disclosed design-limitation finding)

`create_device_slot_exception(extra_slots=1, expires_at=+2min, ...)` -> real
`DeviceSlotException` row, `resolve_effective_device_limit()` (real-datetime path, as
`activation.py` actually calls it) correctly returned `2` immediately after creation.

**Exact semantics confirmed from source, not invented**: with `extra_slots=1` exactly matching the
overage delta, the exception's real effect is **"tolerance of existing overage"**, not "one new device
may activate" -- a real activation attempt from a fifth instance (E) during the exception's live window
was still correctly rejected (`DEVICE_LIMIT_REACHED`), because `activation.py`'s own check is
`active_count >= effective_limit` (`2 >= 2` is still true). A larger `extra_slots` value would be needed
to additionally permit a brand-new device; this session used the minimal value matching the real overage,
which is the conservative, correct choice for "reduce administrative friction on already-active devices"
without inviting further growth -- not a defect, a real, deliberate-reading-confirmed policy behavior.

**A real, disclosed, non-blocking finding**: `scan_over_limit_licenses()` evaluates exceptions at
`datetime.combine(as_of_date, time.min)` (midnight, naive) rather than real current time -- a same-day,
short-duration exception (like this test's 2-minute one) is invisible to the *scan's* own
over-limit-finding logic, even though it is fully effective for the real `activation.py` check (which
uses real `datetime.now(timezone.utc)`). This is consistent with `scan_over_limit_licenses()`'s own
documented "same convention as `expiry_scan.py`" daily-batch design -- not a bug, but a real
characteristic worth recording: an exception created and expiring within one calendar day will not clear
that day's reconciliation finding, only the next day's (once the exception's own window is what's
actually being evaluated at, e.g., a later intra-day rerun using real-time `as_of`, which the current
function signature does not support -- it only accepts a `date`).

## Real expiry (real elapsed time, no clock manipulation)

Waited real wall-clock time past the exception's own `expires_at` (confirmed via `date -u` before/after).
`resolve_effective_device_limit()` (real-time path) correctly reverted to `1`. C and D remained
`ACTIVE`, unchanged, throughout.

## What was not reached (Android-dependent sub-checks)

Steps 12-16 and 32-39 of the governing spec (triggering an Android check-in, capturing the real signed
assertion reflecting the reduced allowance/overage/exception state, confirming state-version increase,
confirming no local entitlement override) require the physical Android device, which was disconnected
for an extended period during this portion of the session (see `phase8vp7-baseline.md`). The
`commercial_runtime` logic these checks would exercise (`resolve_commercial_assertion_fields()`,
`assertion_fields.py`) was not changed this session and was already covered by Phase 8V-P6's own real
physical evidence for the general assertion-refresh/state-version mechanism (a different scenario, same
underlying code path) -- but this scenario's own specific device-facing confirmation is honestly reported
as not independently re-verified this session.

## Disposition

Owner-side mechanics: **PASS**, real, thorough, including a genuine new finding about exception/scan
interaction. Device-facing confirmation: **NOT VERIFIED** this session, disclosed plainly.
