# Phase 9.5D — Commercial Security: Plan

## Governing constraints

Non-Negotiable Principles 12 (no self-approval of exceptions), 14 (no customer data leakage across any commercial document type), 20 (no hardcoded named-individual logic); Milestone 16's segregation-of-duties requirements.

## Approach

1. **Deny-by-default permission extension.** Every new permission (`quotes.*`, `orders.*`, `invoices.*`, `payments.*`, `refunds.*`, `fulfillment.*`, `commissions.*`, `pricing.override`, `discounts.override`) is added to the existing registry (`app/staff/seed_data.py` or wherever Milestone 1's audit finds it) following the exact naming/pairing convention already established by `leads.view_own`/`leads.view_all` in Phase 9.5C — never granted implicitly.
2. **Ownership reuses the proven pattern.** `apply_ownership_filter()`/`customer_visible_to_actor()` (or a documented, minimal extension of the same helpers) gates every Quote/Order/Invoice/Payment/Refund/Commission list and detail route — the same pattern that closed the real IDOR found in Phase 9.5C's `customers/routes.py`, and the same pattern whose fail-open bug (`_lead_or_none`) was found and fixed via real browser testing in that phase. This phase's equivalent routes get the fail-closed version from day one, not discovered via a bug this time.
3. **Segregation of duties enforced at the service layer, not just the route layer** — a requester's own `staff_user_id`/`employee_profile_id` is checked against the approver/confirmer field before any approval, payment confirmation, or refund confirmation commits, independent of whether the UI would ever present that option (Non-Negotiable Principle 4 from Phase 9.5C: UI hiding is not authorization — carried forward here as Principle 12/Milestone 16's segregation-of-duties requirement).
4. **No hardcoded identity.** Every check above is against role/permission/employee-profile state, never an email address or name literal — grep-verified before each milestone closes, matching the exact verification method already used for "management uses the same data" in Phase 9.5C.
5. **IDOR test suite, extended per new resource type.** Following the Phase 9.5C precedent (27+ HTTP-layer IDOR tests): for each new resource (Quote/Order/Invoice/Payment/Refund/Commission), a same-shaped test proves Employee A cannot read/write Employee B's record via UUID, list, count, filter, or approval queue — including the specific leak vectors the spec calls out (document numbering, search).
6. **Real browser + real financial-property validation**, matching the rigor that found 4 real defects in Phase 9.5C's M23 and a real missing-index gap in M24 — this phase's Milestones 19/22-equivalent validation passes are not skipped or reduced in scope just because the domain is unfamiliar.

## What this rules out concretely

No route that grants access based on `current_user.email == "bahaa@..."` or similar; no approval/confirmation/refund action that skips a self-approval check because "the UI wouldn't show the button anyway"; no new list/detail route shipped without an accompanying ownership-filter call, verified the same way Phase 9.5C's fail-open bug was eventually caught — by testing the HTTP layer directly, not just the service layer in isolation.
