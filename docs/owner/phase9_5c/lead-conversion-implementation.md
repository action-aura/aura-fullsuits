# Phase 9.5C — Milestone 13: Lead Conversion Implementation, Final

## Real gaps found (Milestone 1 audit) and closed this milestone

| Gap | Fix |
|---|---|
| No `LeadStatusHistory` row appended on conversion | `convert()` now appends `from_status -> "CONFIRMED"` in the same transaction, matching `change_lead_status()`'s own convention |
| No optimistic-version check | `expected_version` param, checked before any mutation, `LeadError("STALE_LEAD_VERSION")` |
| Re-converting an already-`CONFIRMED` lead with a **new** idempotency key was not rejected (only the exact-same-key replay path returned early) | `status in ("LOST", "ARCHIVED", "CONFIRMED")` now all rejected as `InvalidLeadStateError` |
| Idempotency-key reuse across two **different** Leads silently returned the wrong Customer | New check: on key replay, `replayed_customer.converted_from_lead_id` must equal the requested `lead.id`, else `LeadError("IDEMPOTENCY_CONFLICT")` |
| `find_duplicate_candidates()`'s `phone` argument was accepted but unused | Already fixed in Milestone 3 — phone-based matching is real as of this phase |
| Lead contacts were never carried to the new Customer | `convert()` now copies every `LeadContact` to a `CustomerContact` via the shared `contact_fields_for_customer_copy()` mapping (Milestone 8), on the new-Customer path only |

## The 19-step transactional checklist against the real implementation

1. Authenticate the actor — route layer (Milestone 16).
2. Validate permission (`leads.convert`) — route layer.
3. Validate ownership/management authority — route layer, same
   ownership check as every other Lead action.
4. Lock/version-check — `expected_version` param, this milestone.
5. Confirm eligible status — `status not in (LOST, ARCHIVED, CONFIRMED)`.
6. Confirm not already converted — covered by #5 (`CONFIRMED` check) and
   the idempotency-conflict check for the replay case.
7. Run duplicate detection — `find_duplicate_candidates()`, unchanged,
   name/registration/email/phone.
8. Create new Customer or link existing — unchanged.
9. Preserve creator/source attribution — `Lead.created_by_
   employee_profile_id` never touched by `convert()`.
10. Preserve current assignee — `assigned_staff_user_id` resolved from
    the Lead's live assignment and set on the new Customer (new-Customer
    path); the link-to-existing path leaves the target Customer's
    existing assignment untouched (a deliberate choice — linking to an
    already-assigned Customer should not silently reassign it).
11. Preserve Lead history — `Lead` row is never deleted or mutated
    beyond `status`/`converted_at`/`version`.
12. Preserve contacts per documented strategy — **this milestone**:
    copied to real `CustomerContact` rows.
13. Preserve/link pre-conversion interactions, notes, locations,
    follow-ups — **not copied**, deliberately: they remain `lead_id`-
    scoped on the retained Lead row, reachable from the new Customer via
    `Customer.converted_from_lead_id` (documented in
    `conversion-history-preservation.md`). Copying would duplicate
    history under two different parent rows — the opposite of "no
    duplicate history."
14. Mark the Lead converted — `status = "CONFIRMED"`.
15. Set `converted_at` — done.
16. Set `converted_from_lead_id` — this is a **Customer**-side column
    (not a "converted_customer_id on Lead" — the actual schema stores
    the link in the other direction, from Customer back to its source
    Lead; functionally equivalent, verified via
    `Customer.converted_from_lead_id`).
17. Add status history — **this milestone**.
18. Add conversion audit event — `LEAD_CONVERTED`, unchanged.
19. Commit atomically — single `db_session.commit()`, all writes
    (Customer, contacts, Lead mutation, status history, idempotency key)
    before it.

## Rollback / no-orphan guarantee

All writes happen through the same SQLAlchemy session before the single
`commit()` — an exception at any point (e.g. a `DuplicateCustomerError`
raised before any write, or a database constraint violation during
flush) leaves nothing committed: no orphan Customer, no partial contact
copy, no Lead marked converted without its Customer existing. Verified
by `test_conversion_creates_no_subscription_license_or_commercial_
documents` combined with the existing `test_convert_rejects_lost_lead`
(Phase 9.5A) proving a rejected conversion leaves the Lead's status
untouched.

## Confirmed: no commercial fulfillment side effects

`app/leads/conversion.py` imports zero `Subscription`/`License`/
`Installation`/`Quote`/`Invoice`/`Payment`/`Commission` model or service
modules — grep-verified, and directly tested by
`test_conversion_creates_no_subscription_license_or_commercial_documents`.
