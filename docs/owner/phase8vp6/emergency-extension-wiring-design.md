# Phase 8V-P6 — Emergency Extension Functional Wiring Design

## Ownership and scope

Source of truth for "does an emergency extension exist and is it approved" remains
`commercial_ops.emergency_extensions.EmergencyExtension` (subscription-scoped, MFA/recent-auth gated at
the route layer, audited) -- unchanged. The **stored** `OfflinePolicy.emergency_extension_allowed`/
`emergency_extension_until` columns remain a per-*policy-code* technical default (unchanged, still used
for the shape/validation `OfflinePolicy.__post_init__` and `serialize_policy()` already provide) --
**never written by this fix**. The effective, per-check-in, per-subscription override is computed fresh
inside `checkin.py`, immediately before the policy dict is embedded in this one assertion, and discarded
after -- it is never persisted anywhere.

## New function: `emergency_extensions.get_active_extension()`

`commercial_ops/emergency_extensions.py` already has a private `_active_extension_for(subscription_id,
now)` returning the row or `None`. Add a thin public wrapper `get_active_extension(subscription_id, *,
now=None) -> EmergencyExtension | None` (mirrors the existing `is_emergency_extension_active()`
boolean wrapper, but returns the row so `checkin.py` can read `expires_at`). No change to the private
helper or its query.

## Change in `checkin.py`

After `offline_policy = get_policy_for_license(license_row)` and before `serialize_policy(...)` is
passed into `build_assertion_payload`, look up
`get_active_extension(license_row.subscription_id, now=datetime.now(timezone.utc))`. If found, build
the serialized policy dict as usual via `serialize_policy(offline_policy)`, then overlay two keys in the
resulting **dict** (not the DB row): `emergency_extension_allowed = True`,
`emergency_extension_until = extension.expires_at.isoformat()`. If no active extension, the dict is
passed through unchanged -- `serialize_policy()`'s existing output (whatever the stored policy's own
defaults are) is exactly what ships, preserving today's behavior for every subscription with no active
extension.

## Why this cannot leak between subscriptions

The lookup key is `license_row.subscription_id` -- the exact subscription this check-in's own
installation belongs to. Two different installations under two different subscriptions, even if they
share the same `OfflinePolicy` **code**, each get their own fresh lookup on their own check-in; there is
no shared mutable state anywhere in this path.

## Interaction with `policy_evaluator.py`'s existing extension logic

No client-side change needed for this half -- `evaluate()` already reads
`policy.emergency_extension_allowed`/`policy.emergency_extension_until` from the assertion's own
embedded `offline_policy` object (lines 108-111), which will now correctly reflect the real extension
once Owner populates it correctly. The only client-side change required is the new commercial-hard-state
steps (6-8 in the state matrix doc) checking `emergency_extension_effective` before short-circuiting to
`RESTRICTED` -- see `canonical-commercial-state-matrix.md`.

## Automatic expiry

No explicit "expire" action is needed anywhere -- the override is computed fresh, from
`extension.expires_at`, on every single check-in. Once real elapsed time passes `expires_at`,
`get_active_extension()`'s own query (`EmergencyExtension.expires_at > now`) naturally stops returning
the row on the very next check-in, the override is not applied, and the assertion reverts to the
stored policy's real defaults (`emergency_extension_allowed=False` typically) -- which, combined with
the new commercial-hard-state steps, correctly returns the device to `RESTRICTED` if the underlying
subscription is still commercially non-active.
