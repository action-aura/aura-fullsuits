# Phase 9.5D — Rounding and Allocation Policy

## Rounding mode

`ROUND_HALF_UP` at 2 decimal places (`Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`). This is not a new policy invented for this phase — it's the repository's existing, already-established convention, confirmed by grep before writing the calculator:

- `products/retail/backend/core/retail/pricing.py:84` — `ROUND_HALF_UP at 2 decimal places -- the same rounding rule`.
- `products/retail/backend/api/retail_api.py` (7 call sites) — subtotal/discount/tax/total/refund/balance, all `ROUND_HALF_UP` at 2dp.
- `products/clinic/backend/api/clinic_api.py` — payment amount/outstanding/paid, all `ROUND_HALF_UP` at 2dp.
- `owner/app/commissions/services.py::calculate_commission()` — the exact same `.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` call, already in this codebase.

`app/commercial_sales/calculator.py` reuses this exact mode — no deviation, no new precedent.

## Document-level discount allocation: the deterministic algorithm

`allocate_document_discount(lines, document_discount)`:

1. Compute each line's share proportionally: `line.line_net / eligible_total * document_discount`.
2. Quantize each raw share to the cent (`ROUND_HALF_UP`).
3. Sum the quantized shares. The difference between this sum and the requested `document_discount` (always an integer number of cents, since both are already cent-precision Decimals) is the **rounding remainder**.
4. Distribute the remainder cents one at a time, in **stable order**: lines sorted by `(sort_order, original list position)`. A positive remainder adds one cent to each of the first N lines in that order; a negative remainder (over-quantization) subtracts one cent from each of the first N lines in the same order.

This guarantees:
- **Exact reconciliation**: the sum of allocated per-line discounts always equals the requested document discount exactly, to the cent — never off by a cent due to rounding drift.
- **Determinism**: the same input (same lines, same discount amount) always produces the same per-line allocation, every time — required for reproducible historical documents and for the price-snapshot-immutability property to hold at the document level, not just the line level.
- **Fairness by construction, not by accident**: `sort_order` is the same field used for line display order, so the remainder-cent assignment is visible and explicable to a reviewer ("the first line(s) in display order absorb the rounding cent"), not an opaque hash-based tiebreak.

Verified by `test_document_discount_allocation_deterministic_remainder` and `test_document_discount_allocation_repeatable_for_same_input` (three equal lines splitting a 1.00 discount that doesn't divide evenly by 3 — reconciles to exactly 1.00, reproducibly).

## Payment allocation (Milestone 11) reuses the same boundary-rejection discipline, not the same proportional-split algorithm

Document-discount allocation *automatically* spreads one amount across many lines by formula. Payment allocation (Milestone 11) is the opposite shape — an employee/Finance user *explicitly chooses* how much of a confirmed Payment to apply to which specific outstanding Invoice, one allocation action at a time. `validate_allocation_amount()` provides the boundary checks (cannot exceed unallocated payment, cannot exceed invoice outstanding, must be positive) that every such explicit allocation action must pass through — it does not auto-split a payment across multiple invoices; that remains an explicit, auditable choice per Milestone 11's own design.

## Refund allocation

Similarly explicit, not automatic: `validate_refund_amount()` checks a proposed refund against the invoice's refundable balance (`calculate_refundable_balance()` — invoice total minus already-refunded, floored at zero). No proportional-split logic is needed here since a refund always targets one invoice (optionally one specific payment via `CommercialRefund.payment_record_id`) — there is no multi-invoice refund concept in this phase's scope.
