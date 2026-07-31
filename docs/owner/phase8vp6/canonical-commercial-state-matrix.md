# Phase 8V-P6 — Canonical Commercial State Matrix

## One authoritative resolver, not two

`owner/app/commercial_ops/state_resolution.py::resolve_commercial_state()` is already the one
authoritative Owner-side decision function (Part B's requirement already existed from an earlier
milestone). This phase does not create a second one. It is extended nowhere -- its existing output
(`may_issue_assertion`, `may_check_in_existing_installation`, `state`, `reason_code`) is not changed,
because `checkin.py` doesn't need it: the assertion it already builds already carries the raw
`subscription_status`/`license_status`/`commercial_grace_end` fields the *client's* resolver
(`policy_evaluator.py::evaluate()`) needs. Owner's resolver remains the authority for **reconciliation
reporting** and **activation-time decisions**; the product's `evaluate()` remains the authority for
**local, offline-capable, signed-evidence-driven** decisions -- these were always meant to be two ends
of the same signed pipe, not two competing engines. See spec's own Non-Negotiable Rule 1: "do not create
independent decision engines that can contradict one another" -- satisfied because the client only ever
acts on fields Owner itself already signed, and Owner's own resolver's rules (REVOKED wins, PAST_DUE
before grace stays operational, EXPIRED restricts, emergency extension overrides everything except
REVOKED) are exactly mirrored in the new client-side logic below, not reinvented.

## Client-side effective-state decision (the new logic in `policy_evaluator.py::evaluate()`)

Evaluated in this order, each step short-circuiting:

1. Clock rollback -> `CLOCK_REVIEW_REQUIRED` (unchanged, pre-existing).
2. `installation_status in (SUSPENDED, REVOKED)` -> immediate (unchanged, pre-existing).
3. `license_status in (EXPIRED, REVOKED)` -> `EXPIRED` (unchanged, pre-existing).
4. **New:** `license_status == "SUSPENDED"` -> `SUSPENDED`, unless an effective emergency extension
   covers it -- **no**, per the spec's own Non-Negotiable Rule 8 ("emergency extension... unable to
   override security revocation") and Rule 4 precedence discussion, a security-driven `SUSPENDED` is
   treated the same as `REVOKED` for this purpose: **not** overridable by an emergency extension. Both
   are staff security actions, not payment-timing gaps.
5. **New:** compute `emergency_extension_effective = policy.emergency_extension_allowed and
   policy.emergency_extension_until is not None and now < policy.emergency_extension_until` once, up
   front (still read from the assertion's own embedded, per-check-in-fresh `offline_policy` object --
   no change to where this data lives, only when it's computed).
6. **New:** `subscription_status in ("EXPIRED", "CANCELLED")` and not `emergency_extension_effective`
   -> `RESTRICTED` immediately (commercial decision, independent of `hard_expiry_behavior` -- Part D's
   explicit rule: "do not allow WARN_ONLY technical policy to neutralize an expired commercial
   subscription indefinitely").
7. **New:** `subscription_status == "SUSPENDED"` (distinct from license-level suspension, e.g. a
   subscription-level billing hold) and not `emergency_extension_effective` -> `RESTRICTED` immediately,
   same reasoning as license SUSPENDED but does allow emergency-extension override (a billing-driven
   subscription suspension is not the same class of action as an explicit security `License.status`
   suspension/revocation -- extension override remains meaningful here).
8. **New:** `subscription_status == "PAST_DUE"` and `commercial_grace_end` is set and
   `now > commercial_grace_end` and not `emergency_extension_effective` -> `RESTRICTED` immediately.
9. Otherwise (subscription `ACTIVE`/`PILOT`, or `PAST_DUE` still within its signed grace, or any
   commercial hard-restriction above was overridden by an effective emergency extension): fall through
   unchanged into the existing technical offline-grace timer logic (steps that already existed --
   `ACTIVE_ONLINE`/`ACTIVE_OFFLINE`/`WARNING`/`GRACE_PERIOD`/`RESTRICTED`-on-`hard_expiry_behavior`).

This keeps commercial and technical grace genuinely distinct (Part D): the new steps 6-8 are pure
commercial decisions that never consult `hard_expiry_behavior`/elapsed-offline-time at all; step 9's
existing logic remains the pure technical decision, untouched, for the case where commercial status
doesn't itself demand restriction.

## Existing `LicenseState` values reused, none added

`SUSPENDED`, `EXPIRED`, `RESTRICTED` already exist in
`commercial_runtime/licensing_contracts/state_machine.py`. No new state was needed.
