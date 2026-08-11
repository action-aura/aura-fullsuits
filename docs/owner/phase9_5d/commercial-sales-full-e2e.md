# Phase 9.5D — Milestone 27: Full Local Commercial E2E

Matches the Accounting subsystem's own "Enterprise Acceptance Test" precedent (`accounting-operations.md`'s 18-step full scenario) — one continuous scenario proving the whole commercial-sales/commission system works together as a coherent unit, not merely that each pair of adjacent steps works in isolation (which every earlier milestone's own test file already proves extensively).

## The scenario (`tests/test_phase9_5d_full_commercial_e2e.py`)

Lead created → qualified through the real status machine → Lead-based Quote created → a line added with a price override (triggering a real pending `CommercialApproval`, proven via `unresolved_approvals_for_targets()` returning exactly one row) → Finance approves the exception (a different actor than the requester) → customer acceptance recorded → `resolve_customer_for_accepted_quote()` converts the Lead to a Customer through the canonical Milestone 7 boundary (Lead status becomes `CONFIRMED`) → Sales Order created and confirmed → Commercial Invoice created and issued → Payment submitted and confirmed (by a different actor than who submitted it) → Payment Allocation (full) → **real commission earning** fires automatically (`10%` of the tax-excluded `$900.00` = `$90.00`, matching the price-override amount, not the original catalog price) → commission approved (again, a different actor than the beneficiary) → payout batch created, approved by a third actor, and the entry paid → `fulfill_order()` provisions a **real** `Subscription` (`ACTIVE`) and `License` → a `$450.00` (50%) partial Refund is requested, approved, and confirmed → a **proportional** `-$45.00` commission reversal posts automatically, append-only (the original `$90.00` `PAID` entry is verified byte-for-byte unchanged afterward) → the entitlement consequence for a *partial* refund correctly leaves the Subscription `ACTIVE` (only a full refund suspends, per Milestone 12's `determine_entitlement_consequence()`) → a final sweep confirms all 16 expected `AuditLog` action codes are present for the transitions that actually happened.

## Real gap found and fixed while writing it

The synthetic test product had no `ProductPlatform` association, so `fulfill_order()` correctly rejected it (`FULFILLMENT_NOT_ELIGIBLE`, "plan has no supported platforms configured") — this was the E2E's own test-setup gap (missing the exact seeding `test_phase9_5d_fulfillment.py` already does), not a code defect; the rejection itself is exactly Milestone 13's own real bug fix (the "ALL" platform placeholder) working as designed. Fixed by adding the same `ProductPlatform` seed.

## What this proves that no earlier milestone's tests could

Every value that flows through the chain is *derived*, never hand-supplied at the point of use: the commission basis (`$900.00`, the price-override amount agreed after approval, not the original `$1000.00` catalog price) only exists because the approval workflow, the calculator, and the allocation-triggered earning function all correctly passed the same real number through five documents and three service-layer boundaries. A bug in any one of Milestones 3, 5, 6, 8, 9, 10, 11, 12, 13, or 15 that happened to still pass that milestone's own narrower unit tests (e.g. an off-by-one in how the override price propagates from `QuoteLine` to `SalesOrderLine` to `CommercialInvoiceItem`) would very likely surface here as a wrong `$90.00`/`$45.00` assertion, even if it were invisible to every earlier, narrower test.

## Verification

1/1 pass. Full Owner regression re-run after this milestone (see gate matrix) to confirm no interaction effects with the rest of the suite.
