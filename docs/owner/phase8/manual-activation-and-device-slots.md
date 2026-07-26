# Phase 8 — Manual Activation Approval and Device-Slot Operations (Parts O/P, Milestone 5)

## Activation modes -- additive, opt-in, zero risk to existing behavior

`ActivationPolicy` (`owner_activation_policies`) resolves per-product, same product-specific-then-
global-default lookup pattern as `CommercialPolicy` (Milestone 3): `resolve_activation_mode()`
returns `"AUTOMATIC"` whenever no row exists at all, which is the state of the world for every
product today and for every test written before this milestone. `AUTOMATIC` preserves the exact
Phase 6/7 activation behavior byte-for-byte -- a validated request is approved and signed in the
same call. Only once Owner staff explicitly create a `MANUAL_APPROVAL`/`RISK_REVIEW` policy row for
a product does anything change for that product's *new* installation registrations.

`RISK_REVIEW` is currently handled identically to `MANUAL_APPROVAL` (both gate every new
registration unconditionally) -- it exists as a distinct value so a future risk-scoring signal can
route only flagged requests for review, leaving that distinction itself for whichever milestone
adds real scoring.

## What gets gated, and what never does

Only a **brand-new installation registration** that would otherwise consume a fresh device slot is
gated. A reused installation -- the same device retrying, or a routine reactivation -- is never
newly re-reviewed once it has already been approved once. This is enforced in
`licensing_service/activation.py` by checking `installation.status == "PENDING_ACTIVATION"` in the
code path shared by both the "new registration" and "reused installation" branches, rather than
gating only the new-registration branch: a reused installation that is *still* `PENDING_ACTIVATION`
(a retry while a decision is still outstanding) is never silently forced to `ACTIVE` the way the
pre-existing code unconditionally forced any non-`ACTIVE` reused installation back to `ACTIVE`.

## The PENDING response -- new, additive wire shape

`activation-response-v1.schema.json` gains a third `oneOf` branch: `result: "PENDING"`,
`decision: "PENDING_REVIEW"`, no `signed_assertion`. This is additive (existing `SUCCESS`/`FAILURE`
consumers are unaffected) but it IS a genuinely new shape no client has ever had to handle. **Do not
enable `MANUAL_APPROVAL`/`RISK_REVIEW` against any real Android/Windows build before Milestone 7
ships the matching Kotlin/Windows handling** -- until then this is Owner-side-only capability,
exercised by tests and future staff tooling, never by production traffic.

## No signed assertion is built at gate time -- and none needs to be

`PendingActivation` deliberately does not store a pre-built signed assertion. Approving only flips
`Installation.status` `PENDING_ACTIVATION -> ACTIVE` -- a plain, already-declared
`installations.services` transition. The device's next call (a retried activation, or eventually a
check-in) builds and signs the assertion fresh from current database state, the same "never cached,
always resolved fresh" principle already documented in `commercial_ops/renewal_requests.py`'s module
docstring. This sidesteps having to duplicate the entitlement-resolution/signing logic in the
approval path entirely.

## Self-healing retry, and the idempotency gap it exposed

A client is expected to retry the same logical activation (same `idempotency_key`, fresh
`nonce`/`timestamp`/`request_id` -- nonces are single-use, see `replay.consume_nonce()`) with
backoff. `record_idempotency()`'s cached-response guard only short-circuits when
`cached_response_json` is present, so the `PENDING` write deliberately omits it. A retry with the
same key re-enters `process_activation()`, re-locates the same installation via the existing
device-key-fallback/label-match dedup logic, and re-checks `installation.status`:

- still `PENDING_ACTIVATION` -- returns a fresh `PENDING` response reflecting current reality.
- flipped to `ACTIVE` by an approval that landed in between -- falls straight through to the
  original steps 19-23 (entitlements, sign, `SUCCESS`), no special-casing needed.
- flipped to `DEACTIVATED` by a rejection -- the installation is never found in the same client
  request's device path again the way an approved one is (rejection frees the slot rather than
  leaving something to retry into).

This surfaced a genuine pre-existing gap: `idempotency.record_idempotency()` only ever `INSERT`ed,
because every caller before this milestone recorded exactly one, permanently-terminal result per
key. Finalizing a `PENDING` record to `SUCCESS` now needs a *second* write for the *same*
`(idempotency_key, operation_type)` pair, which hit the table's own `UNIQUE` constraint. Fixed by
making `record_idempotency()` `UPDATE` an existing row in place when one is found, `INSERT` when
none exists -- every pre-Phase-8 caller still only ever hits the `INSERT` branch (no prior row ever
exists for them), so this is non-breaking; confirmed by the full 300+ test suite staying green.

## Device-slot operations (Part P)

Most of the underlying mechanics already existed from Phase 6: `Installation.status` already had a
`REPLACED` terminal state and an `ACTIVE -> REPLACED` transition, and `DEACTIVATED` was already
excluded from the slot-consuming status set. `release_device_slot()`/`replace_device_slot()`
(`commercial_ops/device_slot_ops.py`) are thin, mandatory-reason wrappers around the existing,
unmodified `transition_installation()` -- the same shape as Milestone 4's `cancel_pilot()`/
`revoke_emergency_extension()`: a reason gate plus a single, intention-revealing entry point, not a
new state-machine capability.

`SLOT_CONSUMING_STATUSES` and a `count_slot_consuming_installations()` helper were promoted from a
private constant duplicated only inside `activation.py` into `installations/services.py`, so the
activation protocol, the manual-approval recheck, and the over-limit scan share one definition
instead of three that could silently drift apart.

## Temporary device-slot exceptions

`DeviceSlotException` never edits `License.device_limit` (the permanent, contractual figure).
`resolve_effective_device_limit()` sums every currently-ACTIVE, currently-in-window exception's
`extra_slots` on top of it at check time -- consulted both by the live activation-time device-limit
check and by `approve_pending_activation()`'s defensive recheck. Time-boxed by design
(`MAX_DEVICE_SLOT_EXCEPTION_DAYS = 90`), same "temporary means temporary" spirit as
`EmergencyExtension` (Part N) even though this isn't a commercial extension.

## Over-limit remediation -- explicitly without silent deactivation

`scan_over_limit_licenses()` mirrors `expiry_scan.py`'s shape exactly: report-only by default,
CLI-runnable (`flask commercial device-limit-scan[--apply]`), deduped `InternalNotification`
creation reusing Milestone 3's notification infrastructure. It **never** deactivates, replaces, or
otherwise touches any installation itself -- Part P's explicit instruction. It only ever surfaces a
notification for a human to act on via `release_device_slot()`/`replace_device_slot()`/a
device-allowance renewal, and staff choose which installation to release, never the job.

## Permissions

`activation_policy.manage` is deliberately unassigned to any named role except via the
`SUPER_ADMIN` wildcard -- changing a product's activation mode changes the security posture of every
future activation for that product, the same reasoning Milestone 4 applied to emergency-extension
permissions. `pending_activations.view`/`.decide` and `device_slot_exceptions.view`/`.manage` are
granted to `SUPPORT` (the role already holding `installations.replace_device` from Phase 6);
`VIEWER` gets read-only visibility into both.

## What Milestone 5 deliberately does not do yet

- No routes/CLI for approving/rejecting pending activations or managing device-slot exceptions yet
  (Milestone 6/7) -- service-layer only, reachable from Python/tests/a future CLI command, matching
  how Milestone 4 deferred its own routes.
- No Kotlin/Windows client handling of the `PENDING` decision -- Milestone 7's job alongside
  finalizing the assertion schema more broadly. Do not enable a gating policy against a real client
  build until then.
- No risk-scoring signal distinguishing `RISK_REVIEW` from `MANUAL_APPROVAL` in practice yet -- both
  gate unconditionally today.
- No Scenario 6 (Part AB -- device replacement) or the downgrade-below-active-count exception path
  (Scenario 7's device-side half) physical end-to-end validation yet -- deferred to Milestone 8
  alongside the other Part AB scenarios.
