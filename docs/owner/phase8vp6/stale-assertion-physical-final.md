# Phase 8V-P6 — Stale-Assertion Rejection — Physical Final

## Result: NOT VERIFIED this session

## Why

The real wire-capture middleware (`owner_capture_server.py`) remains observe-only this session, as in
Phase 8V-P5 -- it was not extended with the hold/delay/release capability the governing spec's
preferred method (Part S) requires. This session's real time went to the core enforcement wiring fix
and its two physical scenario proofs instead. No new attempt was made this session, and none is
claimed.

## Note relevant to this session's own fix

The new commercial-enforcement logic does not change assertion signing, `state_version`/monotonicity
semantics, or the stale-assertion rejection path at all -- `assertion_verifier.py`'s signature and
date-validity checks are unchanged; only which `LicenseState` a *validly signed, currently valid*
assertion maps to was changed. A stale-assertion test, when performed, exercises orthogonal code and is
unaffected by this session's changes either way.

## Follow-up required (next session)

Unchanged from Phase 8V-P5: extend the capture middleware with a controlled hold/release capability and
run the real ordering test described in the governing spec's Part S.
