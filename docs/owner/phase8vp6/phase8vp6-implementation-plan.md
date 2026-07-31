# Phase 8V-P6 — Implementation Plan and Timezone Audit (Parts F/G/H summary)

## Implemented (real code, real tests, real regression -- see commit history)

1. `commercial_runtime/licensing_contracts/policy_evaluator.py`:
   - `AssertionEvidence` gains `commercial_grace_end: Optional[datetime] = None` (additive).
   - `evaluate()` gains: `license_status == "SUSPENDED"` hard state (never overridable by emergency
     extension); `subscription_status in (EXPIRED, CANCELLED)` -> `RESTRICTED` unless extension
     effective; `subscription_status == "SUSPENDED"` -> `RESTRICTED` unless extension effective;
     `subscription_status == "PAST_DUE"` past `commercial_grace_end` -> `RESTRICTED` unless extension
     effective. All computed independently of `hard_expiry_behavior` (Part D).
2. `commercial_runtime/licensing_contracts/assertion_verifier.py`: parses `payload.commercial_grace_end`
   into the new `AssertionEvidence` field; malformed value raises `AssertionVerificationError`
   (deny-unknown-state, Non-Negotiable Rule 10), consistent with how `not_before`/`expires_at` are
   already handled.
3. `owner/app/licensing_service/offline_policy.py`: new `serialize_policy_for_subscription()` --
   per-request override of `emergency_extension_allowed`/`emergency_extension_until` from a real,
   active `EmergencyExtension`, never touching the stored `OfflinePolicy` row.
4. `owner/app/commercial_ops/emergency_extensions.py`: new `get_active_extension()` (row-returning
   sibling of the existing boolean `is_emergency_extension_active()`).
5. `owner/app/licensing_service/checkin.py` and `activation.py`: both now call
   `serialize_policy_for_subscription()` instead of `serialize_policy()`.

## Timezone audit (Part G)

Grepped every touched file for `datetime.utcnow()`/naive `datetime.now()`: zero occurrences (one
match is a comment referencing the already-fixed Phase 8V-P5 bug, not live code).
`checkin.py`/`activation.py` already use `datetime.now(timezone.utc)` consistently.
`serialize_policy_for_subscription()` defaults `now` to `app.models.base.utcnow()` (the correct,
shared, timezone-aware helper) when not given. `assertion_verifier.py`'s new
`commercial_grace_end` parse uses the same `datetime.fromisoformat()` pattern already used for
`not_before`/`expires_at`, which already requires/produces timezone-aware values end to end (Owner
always signs ISO-8601 with UTC offset). No new naive-datetime surface introduced.

## Contract/version decision

No bump -- see `assertion-contract-change-decision.md`. Both new fields (`commercial_grace_end`
already signed since Part W; the emergency-extension override reuses existing `offline_policy` keys)
require zero wire-format changes.

## Test summary (Part H)

- `commercial_runtime/licensing_contracts/tests/test_policy_evaluator.py`: 12 -> 23 (+11: license
  SUSPENDED hard state and its extension-non-override, subscription EXPIRED/CANCELLED/SUSPENDED
  restriction, PAST_DUE within/after grace, PAST_DUE with no grace-end set, extension overriding
  EXPIRED, extension's own expiry reverting the override, ACTIVE baseline unaffected).
- `commercial_runtime/licensing_contracts/tests/test_assertion_verifier.py`: 17 -> 20 (+3:
  `commercial_grace_end` absent/present/malformed).
- `owner/tests/test_phase6_checkin_protocol.py`: 10 -> 13 (+3: real HTTP-level check-in with an
  active extension embeds the override AND leaves the stored policy row unmutated; no-extension
  check-in leaves the policy unchanged; a real subscription EXPIRED transition with license status
  left untouched still produces a fresh assertion whose `subscription_status` reflects reality).

## Full regression after the change (before physical validation)

Owner 398/398, commercial_runtime+licensing_contracts 226/226 -- both re-run in full, zero failures,
zero collateral breakage in any pre-existing test. Retail/Clinic backend suites re-run in parallel
(results in `final-regression-report.md`).
