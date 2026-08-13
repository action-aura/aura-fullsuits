# Phase 9.5D — Financial Integrity: Plan

## Governing constraint

Every money value in this phase is server-authoritative, `Decimal`-only, and immutably snapshotted at document-creation time (Non-Negotiable Principles 8, 9, 10). This is the single most safety-critical property of the whole phase — a bug here means wrong revenue/commission numbers, which is worse than an outright crash because it fails silently.

## Approach

1. **One authoritative calculation service** (Milestone 3), built before any document type exists. Every Quote/Order/Invoice line total, document total, discount allocation, tax, refund cap, and commission basis computation routes through this single module — never reimplemented per-route or per-form.
2. **Decimal end-to-end**: request parsing converts client-supplied numeric strings to `Decimal` immediately (never `float`); serialization back to the client uses string representation, never JSON float (matches the spec's "Decimal-safe string serialization" API requirement).
3. **Deterministic rounding and allocation**: `ROUND_HALF_UP` (or the repository's own established policy, if the audit finds one already in use for money elsewhere — e.g. in `commercial_sales` or `commissions` from Phase 9.5A) applied consistently; document-level discount allocated proportionally by eligible line net, with a documented deterministic remainder rule (stable line ordering) so the same inputs always produce the same cent-level split.
4. **Boundary rejection, not silent clamping**: NaN, Infinity, negative quantity, negative document total, discount exceeding gross, refund exceeding refundable, allocation exceeding outstanding — all explicitly rejected with a stable error code, never silently clamped to a "safe" value (clamping hides bugs).
5. **Testing strategy**: table-driven tests covering the boundary rules above, plus multi-line documents with intentionally awkward per-unit prices (e.g. prices that don't divide evenly by quantity or discount percentage) to exercise the rounding-remainder logic deterministically. This is this phase's equivalent of Phase 9.5C's IDOR test suite in rigor.
6. **Snapshot verification**: a test that mutates the live catalog price/tax rate after a Quote/Order/Invoice is created and asserts the historical document's stored values are unchanged (proves Non-Negotiable Principle 10 is real, not just documented).

## What "server-authoritative" rules out concretely

No route handler ever writes `request.json["total"]` (or subtotal/tax/discount/paid/commission/balance) directly into a model column. Every one of those fields is computed server-side from line-level inputs (product/plan reference, quantity, requested discount/override subject to approval) and persisted only after the calculation service produces it.
