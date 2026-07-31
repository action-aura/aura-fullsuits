# Phase 8V-P6 — On-Device Data Preservation — Final

## Result: PARTIAL — real, positive evidence gathered; not a full before/after baseline comparison per
the governing spec's Part E baseline (which was never established, same disclosed gap as Phase 8V-P5)

## Real, positive evidence from this session's own device work

- The rebuild+reinstall (in-place upgrade, `adb install -r`) preserved `firstInstallTime`
  (unchanged from `2026-07-20`) and the installation ID (`c7150980-d45b-4d14-866b-642fb798dceb`,
  unchanged across every one of this session's 4 real check-ins) -- direct evidence that a real Android
  app upgrade, carrying real commercial-logic changes, does not disturb local identity or data.
- Zero patients existed on this installation before this session (a fresh Phase 8V-P5 installation);
  exactly one patient ("Extension Success Patient", `P-1785476289-196`) was created during this
  session's own Scenario 5 positive-effect test, and it was the *only* domain-data mutation performed --
  confirmed via the Patients list showing "No patients yet" immediately before it and showing exactly
  that one record immediately after. No unexpected duplication, no unexpected loss.
- The commercial-state transitions performed this session (subscription EXPIRED, emergency extension
  created and expired) produced zero side effects on domain data at any point -- consistent with the
  architecture (commercial/licensing state and product domain data are structurally separate, confirmed
  by source reading in `phase8vp5-baseline.md`, re-confirmed by this session's own new logic never
  touching product tables).

## What was not done

The full Part E baseline (minimum 3 patients/2 appointments/1 visit/1 prescription/2 invoices for
Clinic; equivalent for Retail) was never created this or the prior session, so a complete before/after
field-by-field comparison across every domain-data category could not be performed.

## Disposition

Reported as PARTIAL rather than PASS, consistent with the honest-disclosure pattern. The evidence that
does exist is real and positive, not merely absent-of-negative-evidence.
