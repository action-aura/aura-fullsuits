# Phase 8V-P6 — Commercial Enforcement Root Cause

## The exact defect, at the exact line

`commercial_runtime/licensing_contracts/policy_evaluator.py::evaluate()`, lines 90-95:

```python
if evidence.installation_status == "SUSPENDED":
    return LicenseState.SUSPENDED
if evidence.installation_status == "REVOKED":
    return LicenseState.REVOKED
if evidence.license_status in ("EXPIRED", "REVOKED"):
    return LicenseState.EXPIRED
```

`AssertionEvidence` (same file, line 61) already has a `subscription_status: str` field. It is already
populated, end to end, from a real signed assertion: Owner already embeds the true
`Subscription.status` in every payload (`owner/app/licensing_service/assertions.py:56`), the field is
already in the client-side allowlist (`assertion_verifier.py:74`), and it is already parsed into
`AssertionEvidence.subscription_status` (`assertion_verifier.py:231`). **`evaluate()` simply never
reads it.** No schema change, no new signed field, no contract version bump is required for this half
of the fix -- the data was already flowing correctly; only the consuming logic was missing.

Separately: `license_status == "SUSPENDED"` is **not** in the hard-state list above at all -- only
`EXPIRED`/`REVOKED` are. This is the precise, single-line reason Phase 8V-P5 was able to complete a
real $100 Retail sale while the license was genuinely `SUSPENDED` in Owner's database: the assertion
correctly said `license_status: "SUSPENDED"`, the client correctly received and parsed it, and then
`evaluate()` silently ignored it because `"SUSPENDED"` was never added to that tuple.

## The emergency-extension gap (confirmed, Phase 8V-P5 finding, re-verified this session)

`evaluate()` lines 108-111 already correctly extend `effective_grace_seconds` when
`policy.emergency_extension_allowed and policy.emergency_extension_until is not None` -- this logic is
correct and requires no change. The gap is entirely on the **server side**:
`owner/app/licensing_service/checkin.py` builds `offline_policy` via
`serialize_policy(get_policy_for_license(license_row))` (`offline_policy.py`), which serializes the
*stored* `OfflinePolicy.emergency_extension_until` column -- a field never written by
`commercial_ops/emergency_extensions.py::create_emergency_extension()`. The two mechanisms
(`EmergencyExtension` business record vs. `OfflinePolicy.emergency_extension_until` technical field)
are real, both correctly implemented in isolation, and simply never connected.

## Why the fix is safe to make server-side, per-request, rather than by mutating `OfflinePolicy`

`OfflinePolicy` rows are looked up by **code** (`get_policy_for_license` -> `assign_policy`'s
`LicenseOfflinePolicyAssignment`), and the same policy code can legitimately be assigned to more than
one license. Writing `extension.expires_at` into the stored `OfflinePolicy.emergency_extension_until`
column would leak one subscription's extension into every other license sharing that policy code --
exactly the risk this session's own governing spec calls out in Part E. The correct fix computes the
override **in memory, per check-in, per subscription**, in `checkin.py`, immediately before serializing
the policy for embedding in this one assertion -- never touching the stored row. See
`emergency-extension-wiring-design.md`.

## Commercial grace vs. technical offline grace (already separately modeled, needs one more field)

`owner/app/commercial_ops/assertion_fields.py::resolve_commercial_assertion_fields()` already computes
`commercial_grace_end` correctly (`past_due_since + policy.payment_grace_days`) for a `PAST_DUE`
subscription, already signs it into every assertion, and it is already in the client allowlist. It is
simply not yet parsed into `AssertionEvidence` or consumed by `evaluate()` -- the same "data already
flows, consuming logic is missing" pattern as `subscription_status`.

## What this means for the fix

No new assertion fields are required for `subscription_status`-driven enforcement (already present).
One field (`commercial_grace_end`) needs to be added to `AssertionEvidence`/`assertion_verifier.py`
(additive, backward-compatible -- absence means "no active PAST_DUE grace window", never a crash).
`checkin.py` needs a small, additive per-request override for the emergency-extension fields it already
serializes. No contract version bump is needed (see `assertion-contract-change-decision.md`).
