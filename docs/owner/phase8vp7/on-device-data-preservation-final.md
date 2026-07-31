# Phase 8V-P7 — On-Device Data Preservation — Final

## Result: PASS for everything actually exercised this session; not a full Part R baseline (never
established across any session, same disclosed gap carried forward)

## Real before/after evidence, this session

**Clinic**: 1 patient at session start ("Extension Success Patient") -> after adding "Post Backup
Patient" (2) -> after restore, back to exactly 1, the *correct* one. 1 invoice created
(`INV-C-1785488518-452`), fully paid ($100.00/$100.00), unchanged across a real `RESTRICTED` cycle, a
denied second-invoice attempt, and the subscription restoration. No duplicate patient, no duplicate
invoice, no duplicate payment at any point.

**Retail**: 1 product at session start (19 in stock) -> after adding "Post Backup Product" (2) ->
after restore, back to exactly 1, the correct one, stock still 19. 1 pre-existing sale
(`SALE-000002`, $100.00) -> real return processed (`RET-000001-5eba23a1`) -> stock correctly restored
18 -> 19, net revenue correctly reduced $100.00 -> $0.00. No duplicate sale, no duplicate return.

**Commercial-state transitions never touched domain data**: every subscription transition performed
this session (Retail EXPIRED->ACTIVE, Clinic EXPIRED->ACTIVE twice) produced zero side effects on
patient/invoice/product/sale records -- confirmed directly by the exact-match counts above before and
after each transition.

## What remains not established

The full Part R baseline (minimum 3 patients/2 appointments/1 visit/1 prescription/2 invoices for
Clinic; 5 products/2 categories/1 supplier/3 sales for Retail) was never created in this or any prior
session -- this session worked with the smaller, real datasets that existed from prior sessions' own
validation work, which is sufficient to prove the *mechanism* (no unexpected loss, no duplication, no
commercial-state leakage into domain data) but not a complete field-by-field sweep across every
category the spec's Part R lists.

## Disposition

PASS for the real preservation mechanism, demonstrated repeatedly and consistently across every
operation this session performed on both products. Not claimed as the full exhaustive baseline
comparison the spec describes.
