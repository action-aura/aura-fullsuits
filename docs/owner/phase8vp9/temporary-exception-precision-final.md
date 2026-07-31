# Phase 8V-P9 — Temporary-Exception Precision — Final

## Canonical precision: timezone-aware timestamp (confirmed from source, not assumed)

- `DeviceSlotException.starts_at`/`.expires_at`: `DateTime(timezone=True)` columns
  (`owner/app/models/activation_governance.py:122-123`).
- `create_device_slot_exception()`: `expires_at: datetime`, `starts_at: datetime | None = None`
  (real timestamp parameters, not `date`).
- `resolve_effective_device_limit()`: real-time evaluation, `as_of: datetime | None = None`, defaults
  to `utcnow()` (timezone-aware) -- the function `activation.py`'s own device-limit check actually
  calls.

Database, service, and the real enforcement path (`activation.py`) all already agreed on
timezone-aware timestamp precision. This closes the question the governing spec poses ("does the
canonical precision mean calendar date or timezone-aware timestamp") -- it is unambiguously timestamp,
confirmed by three independent source locations, not a judgment call.

## The one real mismatch, found and fixed

`scan_over_limit_licenses()` (`owner/app/commercial_ops/device_slot_ops.py`) was the sole outlier: it
truncated to `datetime.combine(as_of_date, datetime.min.time())` (naive midnight) before evaluating
`resolve_effective_device_limit()`. A real, active, same-day short-duration exception -- exactly the
kind created and verified in Phase 8V-P7's own physical Scenario 7 test -- was fully effective for
real enforcement (`activation.py`) but invisible to this scan's own over-limit finding.

## Severity classification

**P2, not P1.** The governing spec's own bar for P1 is "the mismatch may authorize an activation
outside the approved period." This mismatch never touched `activation.py`'s own real-time check --
enforcement was always correct. The bug only caused the *reconciliation scan* to under-report overage
for same-day exceptions (a monitoring/notification gap), never an unauthorized activation. Fixed
anyway, at the same rigor as a P1, because it was cheap, real, and worth closing.

## Fix

`scan_over_limit_licenses()` now accepts an explicit `now: datetime | None = None` parameter
(defaulting to real `utcnow()`) and evaluates `resolve_effective_device_limit()` at `now`, entirely
independent of `as_of` (which remains date-precision, used only for the scan result's own bookkeeping
field -- never for exception-window evaluation). The one real caller (`owner/app/cli.py`'s scheduled
scan command) is unaffected -- it never passed `now`, so it automatically gets the corrected real-time
behavior with no caller change required.

## Tests added (real, passing)

- `test_scan_over_limit_licenses_precision_matches_real_time_not_midnight`: a real, active,
  extra_slots=1, 5-minute exception on a 2-active/limit-1 license is correctly NOT flagged (2 <= 1+1).
- `test_scan_over_limit_licenses_flags_after_exception_expiry`: the same license, once its exception
  has genuinely expired (real elapsed `now` past `expires_at`), IS correctly flagged again.

Both pass: `owner/tests/test_commercial_ops_device_slot_ops.py` -> 10/10 (8 original + 2 new). No
collateral regression in the other real caller's test file
(`test_phase8_security_fraud_controls.py` -> 11/11).

## Boundary semantics (from source, unchanged by this fix)

`resolve_effective_device_limit()`'s own window check is `starts_at <= as_of < expires_at`
(inclusive start, exclusive end) -- confirmed by reading the query directly. Not altered this session;
already correct.

## Disposition

Fixed, tested, real. Enforcement (`activation.py`) was never wrong; only the reconciliation scan's own
detection of same-day exceptions was blind to them. Closed.
