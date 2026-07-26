# Phase 8 — Pilot Lifecycle and Emergency Extension Design (Parts M/N, Milestone 4)

## Pilot lifecycle

`PilotRecord` (`owner_pilot_records`, unique on `subscription_id`) is metadata layered on top of an
existing `Subscription` already in `PILOT` status, created the normal way via
`create_subscription()`/`transition_subscription()`. It never duplicates or replaces `Subscription`
— it only adds fields a pilot needs that a regular paid subscription doesn't: allowed workflows,
success criteria, exit-rollback plan, sales/support ownership, and extension tracking.

State machine (`commercial_ops/pilot_lifecycle.py`):

```
DRAFT -> APPROVED -> ACTIVE -> EXTENDED (self-loop) -> CONVERTED
                            \-> COMPLETED
                            \-> CANCELLED
```

`extend_pilot()` enforces all four constraints Part M requires explicitly, not by convention:

- non-empty `reason`
- `new_end_date` strictly after the current `pilot_end`
- a hard cap: `extension_count < max_extensions_allowed` (default 2) — "no indefinite rolling
  pilot"
- every extension writes an append-only `PilotExtension` row (`previous_end_date`/`new_end_date`/
  `reason`/`approved_by_staff_user_id`), never overwriting history

`complete_pilot()` (no-conversion outcome) and `cancel_pilot()` (requires a reason) both also call
the existing, unmodified `transition_subscription()` to move the underlying subscription
`PILOT -> COMPLETED` / `PILOT -> CANCELLED` — both already permitted in
`subscriptions.services.VALID_TRANSITIONS` before Phase 8, not something this milestone widens.

## Pilot conversion — deliberately not a standalone action

Converting a pilot to paid is **not** implemented as a status flip on `PilotRecord`. It goes through
the real, unmodified Milestone 2 renewal pipeline:

1. Create a `RenewalRequest` against the pilot's subscription (`create_renewal_request()`).
2. Walk it through the normal quote/confirm/pay states, then `approve_renewal_request()`
   (separation-of-duties: the creator can never also approve) and `apply_renewal_request()`
   (payment/recent-auth gated at the route layer, row-locked, concurrency-safe).
3. Only once that renewal is genuinely `APPLIED` — meaning the subscription has already flipped
   `PILOT -> ACTIVE` inside that same transaction — does `mark_pilot_converted()` update
   `PilotRecord.status` to `CONVERTED` and stamp `converted_renewal_request_id`.

`mark_pilot_converted()` raises `PilotLifecycleError` if handed a renewal that isn't `APPLIED` yet,
or one that belongs to a different subscription. It never grants paid status itself — it purely
records that the real pipeline already did.

This mirrors the Milestone 2 security lesson directly: a conversion needs the same assurance level
(separation-of-duties, payment confirmation, recent-auth) as any other renewal, so it is built by
*reusing* that pipeline rather than adding a second, weaker "convert this pilot" action that would
need to reimplement all of those guarantees correctly a second time.

`_GRADUATING_SUBSCRIPTION_STATUSES = frozenset({"PILOT"})` was added alongside the existing
`_REVIVABLE_SUBSCRIPTION_STATUSES` in `renewal_requests.py`'s `apply_renewal_request()`, purely so a
pilot graduating to `ACTIVE` gets the same "flip to ACTIVE + write `SubscriptionStatusHistory`"
bookkeeping a revival gets. Unlike `_REVIVABLE_SUBSCRIPTION_STATUSES`, this is not itself a new
security boundary — `PILOT -> ACTIVE` was already unconditionally permitted in the shared
`subscriptions.services.VALID_TRANSITIONS` table before Phase 8.

## Emergency extensions — explicitly not a bypass

`EmergencyExtension` (`owner_emergency_extensions`) is an explicit, short-lived, staff-created
override record consulted only by `resolve_commercial_state()`'s caller — it never writes to
`Subscription`, `License`, or `RenewalRecord`, and never marks a payment confirmed. Part N's own
framing: exceptional support cover, not a second commercial pathway.

`commercial_ops/emergency_extensions.py` enforces, regardless of caller:

- `MAX_EMERGENCY_EXTENSION_HOURS = 72` — hard cap, rejects `duration_hours` outside `1..72`
- non-empty `reason` (mandatory on both create and revoke)
- **no stacking** (v1 simplification): rejects creating a new extension while an `ACTIVE`,
  unexpired one already exists for the same subscription — supersede by revoking first, never
  silent overlap
- refuses to create an extension tied to a `REVOKED` license

Permission (`emergency_extensions.create` / `.revoke`) and MFA/recent-auth enforcement are deferred
to the route layer (not yet built — Milestone 5/6 will add routes for both pilots and emergency
extensions), since `require_recent_auth` needs a Flask request context a pure service function does
not have. Both new permissions are deliberately **not** assigned to any named role in
`seed_data.py` — reachable only via `SUPER_ADMIN`'s `"*"` wildcard, reflecting how exceptional this
path is meant to be.

## `resolve_commercial_state()` override

Extended with an optional `emergency_extension_active: bool = False` keyword (Milestone 1's
function stays pure/I/O-free — it never queries `EmergencyExtension` itself;
`emergency_extensions.is_emergency_extension_active()` is the only place that looks the row up, and
callers pass its result in). Internally the function now delegates to `_resolve_base()` for its
existing branch logic unchanged, then applies the override as a `dataclasses.replace()` on the
returned decision:

- `REVOKED` is resolved and returned **before** the flag is even consulted — an emergency extension
  can never override an explicit signed security revocation, per `EmergencyExtension`'s own
  docstring commitment.
- For every other state, an active override sets `may_issue_assertion` and
  `may_check_in_existing_installation` to `True` and clears `required_action`, with the
  `reason_code` suffixed `_EMERGENCY_EXTENSION_ACTIVE` so audit/UI can distinguish "genuinely
  active" from "active because of an emergency override."

## What Milestone 4 deliberately does not do yet

- No routes or CLI for pilots/emergency extensions yet (Milestone 5/6) — service-layer only, so far
  only reachable from Python/tests, matching how Milestone 1's state-resolution service shipped
  before Milestone 2 gave it a route.
- No caller yet actually threads `is_emergency_extension_active()` into the activation/check-in
  routes' call to `resolve_commercial_state()` — the override parameter exists and is proven correct
  in isolation, but wiring it into the live request path is route-layer work for a later milestone.
- No Scenario 5/6 (Part AB — "pilot conversion", "emergency extension") physical end-to-end
  validation yet — deferred to Milestone 8 alongside the other Part AB scenarios.
