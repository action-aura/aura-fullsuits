# Phase 8 — Renewal Lifecycle Design (Parts C/E/K, Milestone 2)

## What this document covers

The renewal workflow engine in `owner/app/commercial_ops/renewal_requests.py`, its route layer in
`owner/app/commercial_ops/routes.py`, and a real pre-existing security bug it uncovered and fixed
in `owner/app/auth/session.py`.

## State machine

```
DRAFT -> QUOTED -> AWAITING_CONFIRMATION -> AWAITING_PAYMENT -> PAYMENT_RECORDED -> APPROVED -> APPLIED
  |         |               |                      |                   |             |
  v         v               v                      v                   v             v
CANCELLED CANCELLED   REJECTED/CANCELLED   CANCELLED/VOIDED    REJECTED/CANCELLED  CANCELLED/VOIDED
```

Every non-APPROVED/APPLIED transition goes through the generic `transition_renewal_request()`
(mirrors `transition_subscription()`/`transition_license()`'s established shape: validate against
`VALID_TRANSITIONS`, write a status-history row, commit, audit-record). `APPROVED` and `APPLIED`
each have their own function because each does more than a single-column status change.

## Separation of duties (Part Y)

`approve_renewal_request()` unconditionally rejects `actor_staff_user_id == renewal.created_by_staff_user_id`
— whoever created a renewal request may never also approve it. This is enforced in the service
layer (not just hidden behind a UI control), so it holds even if a future caller reaches the
service directly. Verified via HTTP in `test_self_approval_rejected_via_http`.

## The atomic apply transaction (Part E)

`apply_renewal_request()` is the one function in this module that does more than a status change.
Concurrency safety has two independent layers, both proven with real tests (not mocked):

1. **`SELECT ... FOR UPDATE`** on both the `RenewalRequest` and `Subscription` rows. A second
   concurrent call for the *same* renewal request blocks on the row lock until the first
   transaction commits, then re-reads the now-committed row and fails the status check (no longer
   `APPROVED`). Proven in `test_concurrent_apply_of_same_renewal_request_only_one_succeeds` — two
   real OS threads, two real Postgres connections (via `scoped_session`'s thread-local scoping),
   racing to apply the same renewal request. Exactly one succeeds; the subscription's term is
   extended exactly once, never twice, regardless of which thread wins.
2. **A term-staleness recheck**: `renewal.current_term_end` (snapshotted at request-creation time)
   must still equal `subscription.end_date` at apply time. This catches a *different* renewal
   request for the same subscription having already applied — the row lock alone wouldn't catch
   this, since two different `RenewalRequest` primary keys never conflict with each other. Proven
   in `test_second_renewal_against_stale_term_rejected`.

Everything in the transaction — term dates, plan, device allowance, reviving an
EXPIRED/PAST_DUE/SUSPENDED subscription back to `ACTIVE`, creating the linked `RenewalRecord`,
updating the `RenewalRequest` itself to `APPLIED` — happens before a single `db_session.commit()`
call, per spec Part E step 22 ("commit once"). `record_renewal()` (the existing Phase 5 function)
is deliberately *not* called from inside this transaction, since it commits on its own; the
equivalent logic is inlined instead.

## Renewal after expiry: widening the subscription state machine

Scenario 2 of the governing spec's Part AB ("renewal after expiry") requires an `EXPIRED`
subscription to become `ACTIVE` again. Before this milestone, `EXPIRED` was a terminal state in
`owner/app/subscriptions/services.py`'s `VALID_TRANSITIONS` (`"EXPIRED": set()`), which would have
made that scenario structurally impossible. Widened to `"EXPIRED": {"ACTIVE"}` — the state machine
now defines what's *possible*; who is *allowed* to trigger it is the route-level RBAC layer's job,
consistent with every other transition in that table (e.g. `ACTIVE -> SUSPENDED` is also generic,
gated by permission checks elsewhere, not by the transition table itself). In practice only
`apply_renewal_request()` exercises this path today. Full Owner regression suite reconfirmed green
after this change.

## A real pre-existing bug found and fixed: MFA session mix-up

Writing the first test that needed `require_recent_auth` to actually *succeed* immediately after a
fresh MFA login (every existing test of that decorator either doesn't need the positive case, or
deliberately manufactures an *expired* `mfa_verified_at`) surfaced a real bug in
`owner/app/auth/routes.py`'s `mfa_verify_submit()`:

```python
raw_token = create_session(staff)   # creates a NEW StaffSession row (session-fixation defense)
mark_mfa_verified()                 # marks... the OLD, pre-MFA session
```

`create_session()` returns a raw token but never updates Flask's request-local `g` cache.
`mark_mfa_verified()` calls `current_session()` → `load_current_staff()`, which checks `g` first;
finding nothing cached yet, it falls back to `request.cookies.get(COOKIE_NAME)` — the cookie the
*client sent on this request*, i.e. the old pre-MFA session, since the new cookie is only set on
the *response* about to be returned. The client ends up holding a session token that was **never**
marked as recently-authenticated, permanently, for the lifetime of that session — a real,
previously-undetected defect affecting every `require_recent_auth`-gated action (payment/renewal
approval, signing-key rotation, license issuance, and now renewal apply) taken shortly after a
fresh login.

Fixed minimally in `create_session()` itself: after creating and committing the new session row,
set `g.staff_session`/`g.staff_user` to it directly, matching exactly what `load_current_staff()`
itself would set. This makes "the session I just created is the current session for the rest of
this request" hold unconditionally for every caller of `create_session()`, not just this one call
site — the same one-line-root-cause, minimal-fix discipline used throughout this project's prior
phases. Full Owner regression suite reconfirmed green after the fix (this is a change to a
security-critical path and was treated with the same care).

## What Milestone 2 deliberately does not do yet

- No `QUOTED`/`AWAITING_CONFIRMATION`/`AWAITING_PAYMENT` business-process UI — the state machine
  supports the full pipeline, but no template exists to drive a human through it yet (JSON API
  only this milestone; see `owner/app/commercial_ops/routes.py`'s module docstring).
- No reject/void/cancel route wiring beyond the generic `/transition` endpoint (which already
  supports them — just no dedicated confirmation-dialog UI).
- No physical Scenario 1/2 (Part AB) end-to-end validation against a real product installation
  yet — this milestone proves the Owner-side transaction is correct and race-safe; wiring a real
  device's check-in to actually pick up a renewed term (which `checkin.py` already does for free,
  by resolving entitlements fresh on every call — see `renewal_requests.py`'s module docstring) is
  a physical-validation pass, not a code change, and is deferred to Milestone 8 alongside the rest
  of Part AB's scenarios so all of them get validated together rather than piecemeal.
