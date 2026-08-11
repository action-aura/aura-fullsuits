# Phase 9.5D — Milestone 23: Security/IDOR/Segregation/Tampering Pass

Matches Phase 9.5C's own dedicated security-pass precedent (`test_phase9_5c_api_idor.py`) — a focused pass proving the ownership/permission wiring Milestones 5–19 built is actually enforced at the HTTP layer, not merely correct in service-layer isolation.

## Real gap found and closed: cross-customer payment allocation

`allocate_payment()` (Milestone 11) validated `payment.status == "CONFIRMED"` and `payment.currency == invoice.currency`, but never verified `payment.customer_id == invoice.customer_id`. This is not an access-control gap in the usual IDOR sense (both the payment and the invoice are visible/actionable to the same `FINANCE` actor by design) — it is a missing *business-rule* check that would let a real mistake, or a tampered `payment_record_id` in an API/web request body, allocate Customer A's confirmed money against Customer B's invoice. Fixed with a new `PAYMENT_CUSTOMER_MISMATCH` error, proven at both the service layer (direct call) and the API layer (`POST /api/operations/v1/allocations` with a cross-customer `payment_record_id` → `400 PAYMENT_CUSTOMER_MISMATCH`).

## Coverage extended beyond Milestones 18/19

Milestones 18/19 already proved Quote IDOR (peer Quote → 404) and a handful of permission-denial checks (`orders.approve`, `payments.confirm`, `commissions.view_all`). This milestone extends the same IDOR proof to Orders and Invoices (both API and web routes), and adds:

- **Permission-check-before-existence-check ordering**: `POST /api/operations/v1/refunds/<nonexistent-id>/approve` and `.../commissions/<nonexistent-id>/approve` both return `403` for a `SALES` actor, not `404` — proving `@require_permission` fires before the route body ever queries the database, so a permission-denied actor learns nothing about whether the target id even exists.
- **Stale-version tampering**: `POST /orders/<id>/confirm` with a forged `version: 999` returns `409 STALE_VERSION`, not a silent success — optimistic locking is real, not decorative, on the order-confirmation path specifically (already proven for quotes in Milestone 18).

## What was deliberately not re-tested here

Refunds/Payments have no owner-vs-all distinction to IDOR-test — `refunds.create`/`refunds.approve`/`payments.view`/`payments.create` (aside from the Milestone 18 `payments.create` dual-grant) all sit on `FINANCE` only (per the Milestone 16 SoD matrix); there is no lower-privileged single-owner actor whose peer-record access needs blocking the way `SALES` needs blocking on Quotes/Orders/Invoices. Confirmed by re-reading `commercial-sales-sod-matrix.md`, not re-derived from scratch.

## Test coverage

`tests/test_phase9_5d_security_idor.py` — 8 tests: peer Order/Invoice IDOR (API + web), permission-denial-before-404 ordering for refunds/commissions/payout-batches, the new cross-customer allocation block (service layer + API layer), and stale-version tampering on order confirmation.
