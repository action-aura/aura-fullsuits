# Phase 9.5A Milestone 3 — Multi-Device Licensing Policy Model

## What already exists (reused, not touched)

- `License.device_limit` — the real, enforced, single source of truth for "how many active devices may
  this license have right now."
- `resolve_effective_device_limit(license_row, as_of)` (`device_slot_ops.py`) — `device_limit` + active
  in-window `DeviceSlotException.extra_slots`. Still the only function that computes the real
  enforceable number.
- `ActivationPolicy` / `resolve_activation_mode()` — product-specific-then-global-default lookup
  (`effective_date`/`retired_date`, not a live FK), resolving to `AUTOMATIC`/`MANUAL_APPROVAL`/
  `RISK_REVIEW`. `PendingActivation` holds a gated new-installation registration for review.

Both are real, hardened, already-enforced in `owner/app/licensing_service/activation.py`'s live code
path. Neither is modified this phase.

## What's genuinely missing: per-platform sub-limits and combination rules

Nothing today can express "2 total devices, but at most 1 Windows and at most 1 mobile" — only the flat
total (`device_limit`) exists. This is the real gap Milestone 3 closes, following the exact same
architectural pattern as `ActivationPolicy` (a resolved lookup, not a stored per-installation
assignment, so a policy correction affects future activations without a data migration).

## New models (schema only this phase — see the enforcement-wiring boundary below)

```
device_policy_profiles
  id, profile_code (unique), plan_id (nullable FK -> owner_plans, NULL = global default),
  max_total_devices (int, nullable = "no additional cap beyond License.device_limit"),
  approval_required_after_device_number (int, nullable — e.g. 1 means the 2nd+ device needs approval
    regardless of the product-level ActivationPolicy.mode; NULL = defer entirely to ActivationPolicy),
  effective_date, retired_date, notes, created_by_staff_user_id, created_at

device_policy_platform_rules
  id, device_policy_profile_id (FK), platform_category (WINDOWS | ANDROID | IOS | MOBILE — MOBILE is
    the combined Android+iOS cap the spec's own example needs: "mobile category maximum: 1"),
  max_devices (int), created_at

subscription_device_policy_overrides
  id, subscription_id (FK, unique-per-active-override enforced at the service layer, not a DB
    constraint, matching DeviceSlotException's own precedent of time-windowed rows rather than a
    hard single-row uniqueness),
  max_total_devices (nullable override), approval_required_after_device_number (nullable override),
  reason (text, required), approved_by_staff_user_id (FK, required — an override always needs a named
    approver, unlike the plan-level default which is just commercial configuration),
  effective_from, effective_until (nullable = open-ended),
  created_by_staff_user_id, created_at
```

## Resolution function (design, mirrors `resolve_activation_mode()`)

`resolve_device_policy(subscription, as_of) -> EffectiveDevicePolicy` (a plain dataclass, not a model):
1. Start from the subscription's plan's `device_policy_profiles` row (or the global-default row where
   `plan_id IS NULL`, or an all-open policy — `max_total_devices=None`, `platform_rules=[]` — if no row
   exists at all, exactly matching `ActivationPolicy`'s own "no row = preserve prior behavior" rule).
2. Apply any currently-active (`effective_from <= as_of < effective_until`) `subscription_device_policy_overrides`
   row for that subscription, field-by-field (an override field of `NULL` means "keep the plan
   default for this field", not "set to unlimited").
3. Return `EffectiveDevicePolicy(max_total_devices, platform_rule_map, approval_required_after_device_number)`.

## Explicit enforcement-wiring boundary (foundation-phase honesty)

This phase implements the real schema, the real resolution function, and real tests proving it resolves
correctly — but does **not** wire `resolve_device_policy()` into the live
`owner/app/licensing_service/activation.py` request path this phase. Reasoning: that file is the exact
real, physically-proven Phase 8/8V-P9 enforcement code; changing its control flow to consult a second
policy source is a real behavioral change to the licensing critical path, and the governing instruction
for this phase is explicit ("do not weaken the completed Phase 8 licensing architecture" / "preserve
signed assertions, stale/replay rejection"). `resolve_effective_device_limit()` remains the only thing
that gates a real activation this phase. The new policy layer is real, tested, and ready to be wired in
by a later phase (9.5B+) once this exact same regression suite can prove the wiring doesn't regress
Phase 8V-P9's physical stale-assertion/device-limit evidence. Recorded here, not hidden.

## Example resolved policy (matching the spec's own example)

Professional plan profile: `max_total_devices=2`, platform rules `WINDOWS: 1`, `MOBILE: 1`,
`approval_required_after_device_number=NULL` (defer to `ActivationPolicy`). A subscription on this plan
requesting a 3rd device: `resolve_device_policy()` reports `max_total_devices=2` exceeded — a real,
computable fact this phase can test — even though the live activation path doesn't consult it yet.

## Required invariants (all satisfied by this design)

- Active count cannot silently exceed policy: policy resolution never mutates data, only reports —
  matches `scan_over_limit_licenses()`'s own report-only precedent.
- Policy reduction doesn't erase installations: no code path in this phase ever deactivates an
  installation as a side effect of a policy change.
- Policy changes are versioned/audited: every `device_policy_profiles`/`subscription_device_policy_overrides`
  row is itself an immutable historical fact (new row on change, `effective_date`/`retired_date` or
  `effective_from`/`effective_until` bound it in time — nothing is ever `UPDATE`d in place except
  setting a `retired_date`/`effective_until` to close out an old row).
- Employees (SALES) cannot change policies — new permission `device_policy.manage` required
  (Milestone 17), not granted to SALES.
- Only authorized management roles approve overrides — `subscription_device_policy_overrides.approved_by_staff_user_id`
  is a required, non-nullable FK.
- Future iOS policy cannot activate an unsupported product build — `platform_category=IOS` rules can
  exist in the policy table today (commercial configuration), but `ProductPlatform.supported` (existing
  catalog table) remains the real gate on whether iOS is buildable/activatable at all — policy alone
  never grants technical capability.
- No direct route mutation of authoritative counts — `License.device_limit` is never written by any
  code this phase introduces.
