# Phase 8V-P7 — Stale-Assertion Rejection — Physical Proof — Final

## Result: NOT VERIFIED this session

## Why

The real wire-capture middleware remains observe-only, not extended with a hold/delay/release
capability this session either (same disclosed gap as Phase 8V-P5/P6). This session's real device time
went to the artifact-alignment smoke checks (Scenario 3/5 reconfirmation) and Scenario 2/6/7 work
before the physical device disconnected for an extended period, leaving no further device time for a
new controlled-ordering test attempt.

## What is NOT claimed

No stale-assertion rejection evidence, physical or otherwise, is claimed for this session. No forged
signature, no manually-edited assertion, and no unit-test-only evidence is substituted.

## Relevant, unchanged note

This session's fix does not touch assertion signing, monotonicity, or the stale-assertion rejection
path at all -- confirmed by source (only `policy_evaluator.py`'s state-mapping logic and
`checkin.py`/`activation.py`'s policy-serialization call sites changed; `assertion_verifier.py`'s
signature/date-validity checks are unchanged except for the additive `commercial_grace_end` parse,
which does not affect staleness/monotonicity logic).

## Follow-up required (next session)

Extend `owner_capture_server.py` with a controlled hold/release capability and run the real ordering
test described in the governing spec's Part M, once the physical device is reliably connected.
