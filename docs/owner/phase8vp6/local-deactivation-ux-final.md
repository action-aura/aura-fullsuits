# Phase 8V-P6 — Local Deactivation UX — Final

## Result: unchanged from Phase 8V-P5's decision, reconfirmed

No source affecting deactivation/reactivation changed this session (this phase's changes are entirely
in `policy_evaluator.py`, `assertion_verifier.py`, `checkin.py`, `activation.py`,
`offline_policy.py`, `emergency_extensions.py` -- none touch `deactivation.py`,
`state_machine.py`'s `DEVICE_DEACTIVATED` transitions, or `LicensingScreen.kt`). The prior session's
analysis and decision therefore stand and are not re-derived from scratch:

- **Intent (source-confirmed, unchanged)**: case A -- explicit local deactivation requires fresh
  activation via the license key. `commercial_runtime/licensing_contracts/deactivation.py`'s own
  docstring, `TERMINAL_UNTIL_REACTIVATION = frozenset({REVOKED, DEVICE_DEACTIVATED})`, and the
  device's own confirm-dialog copy ("You will need to reactivate with a license key") all agree.
- **Real, disclosed P2 gap (unchanged)**: the state machine defines a legitimate
  `DEVICE_DEACTIVATED -> reactivation_requested -> ACTIVATION_REQUIRED` transition, but
  `LicensingScreen.kt` never triggers it and never shows the key-entry form when
  `state == DEVICE_DEACTIVATED` -- no in-app path currently reaches the reactivation flow the product
  itself promises.
- **No production code changed**, per the governing spec's own explicit instruction for case A ("do not
  change production code, classify as accepted P2/UX limitation"). This session's spec text goes
  further and allows implementing "the smallest UI route to the existing fresh activation flow" if
  intent is A -- deliberately not exercised this session: this phase's real engineering budget went to
  the two P1-class commercial-enforcement gaps (the actual mandate of this phase), and a Kotlin-only UI
  navigation change would need its own physical re-validation cycle (deactivate a real device, confirm
  the new route, confirm reactivation) that was not available within this session's remaining time.
  Recorded as a real, deliberate scope decision, not an oversight.

## Disposition

P2, disclosed, unfixed, carried forward to the residual risk register unchanged in substance from
Phase 8V-P5. Does not block Phase 8 progress -- the spec's own text is explicit that this class of
finding should not block when a fresh-activation path is possible in principle (it is; only its
in-app discoverability is the gap).
