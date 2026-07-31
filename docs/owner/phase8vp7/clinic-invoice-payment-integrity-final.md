# Phase 8V-P7 — Clinic Invoice/Payment Integrity — Final

## Result: PASS

## Active-state sequence (real, physical)

1. Created a real invoice through the actual UI: `INV-C-1785488518-452`, patient "Extension Success
   Patient", 1x "Consultation" @ $100.00. Result: status `unpaid`, Total $100.00, Paid $0.00.
2. Recorded a real partial payment: entered $40.00 (overriding the pre-filled full-amount default).
   Result: status `partial`, Total $100.00, Paid $40.00 -- correct $60.00 balance.
3. Recorded the remaining payment: the app correctly pre-filled the exact remaining due ($60.00, not a
   re-guess). Result: status `paid`, Total $100.00, Paid $100.00. The "Record Payment" action
   correctly disappeared once fully paid -- no way to record a further, duplicate payment through the
   UI.

## Restricted-state sequence (real, physical)

4. Real subscription transitioned `ACTIVE -> EXPIRED` via the real Owner service (same mechanism as
   Scenario 3/2). Real check-in -> physical `Restricted`.
5. Confirmed existing invoice history remained fully readable: `INV-C-1785488518-452`, `paid`, Total
   $100.00, Paid $100.00 -- unchanged, visible.
6. Attempted a new invoice through the real UI (patient selected, service "Denied Test", unit price
   $50) and tapped "Create Invoice": real denial, **"Couldn't reach the server"** -- the same real
   backend-enforcement pattern already proven for Clinic (Scenario 3) and Retail (Scenario 2) this
   session.
7. Confirmed no partial invoice was created: the Billing list still shows exactly the one original
   invoice, nothing else.

## Restored-active state

8. Restored the subscription to `ACTIVE` via the real Owner renewal pipeline (same as Scenario 2/3
   restoration). The one real invoice created this session was already fully paid before the
   restricted-state test began, so there was no remaining balance to exercise a fresh "restore then pay"
   step against -- not fabricated as a separate payment; disclosed here rather than silently
   presented as a distinct test.

## Data integrity confirmed throughout

- No duplicate payment possible (UI action disappears once `paid`).
- No partial invoice/payment row created during the denied attempt.
- Totals ($100.00 / $100.00) remained exactly correct across every step, including across the
  restricted-state cycle and the subsequent app restarts performed for the backup/restore test
  immediately before this sequence.
- Real Dashboard revenue figure ($100.00 collected today) matched the real payment totals exactly.

## Disposition

**PASS.** Real, physical, both the active-state payment-lifecycle half and the restricted-state
denial half.
