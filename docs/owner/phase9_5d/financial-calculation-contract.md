# Phase 9.5D — Milestone 3: Financial Calculation Contract

`app/commercial_sales/calculator.py` — the one authoritative server-side financial calculation service for Quote/SalesOrder/CommercialInvoice line and document totals. `app/commercial_sales/errors.py::CommercialSalesError` carries every stable code this module and the rest of Phase 9.5D's service layer raises.

## Public functions

- `calculate_line(LineInput) -> LineResult` — quantity × effective unit price (override if present, else catalog price), minus this line's own discount. Rejects non-positive quantity, non-finite/negative price, discount exceeding gross.
- `allocate_document_discount(lines, document_discount) -> list[Decimal]` — deterministic proportional allocation across lines (see `rounding-and-allocation-policy.md`).
- `calculate_document(line_inputs, *, document_discount, tax_total) -> DocumentResult` — the single entry point every Quote/SalesOrder/CommercialInvoice create/edit route calls. Computes subtotal, discount_total, tax_total, total, and per-line final discount/net.
- `calculate_refundable_balance(invoice_total, already_refunded) -> Decimal` — floors at zero, never negative.
- `validate_refund_amount(amount, refundable_balance) -> Decimal` — rejects non-positive or over-limit amounts.
- `validate_allocation_amount(amount, *, unallocated_payment_balance, invoice_outstanding_balance) -> Decimal` — rejects non-positive or either-limit-exceeding amounts.
- `resolve_invoice_status(*, current_status, invoice_total, allocated_payment_sum) -> str` — matches `docs/owner/phase9_5a/payment-and-fulfillment-contract.md`'s existing formula, extended (Milestone 11) to work from allocated-payment sums.
- `validate_currency(currency) -> str` — 3-letter alphabetic ISO-4217-shaped code, uppercased. Stricter than `app/leads/validation.py`'s length-only check (that field is a non-financial estimate; this one gates real money movement).

## Decimal end-to-end

Every function takes and returns `Decimal`. Route handlers convert client-supplied numeric strings to `Decimal` immediately on parse (`Decimal(str(request_value))`, never `float(request_value)` then `Decimal`) and serialize back to the client as a string, never a JSON float — matching the API requirement "Decimal-safe string serialization." Every `commercial_sales` model column is `Numeric(12,2)` (a real fixed-precision DB type); Decimal is never round-tripped through `float` anywhere in this module or its callers, unlike Retail/Clinic's own `float(...)` conversions (which exist only for those products' own DB-layer constraints, not because floats are safe in general).

## Boundary rejection, not silent clamping

NaN, Infinity, -Infinity, negative quantity, negative price, negative document total, discount exceeding gross, refund exceeding refundable, allocation exceeding either limit — all raise a stable `CommercialSalesError` code. None are silently clamped to a "safe" value; clamping would hide a client-side or upstream bug behind an incorrect-but-plausible-looking number, which is worse than an explicit error in financial code.

## What "server-authoritative" rules out concretely

No route handler ever writes a client-supplied `subtotal`/`discount_total`/`tax_total`/`total`/`amount_paid`/`amount_refunded`/`commission_amount`/`outstanding_balance` directly into a model column. Every one of those fields is the *output* of a call into this module (or, for commission, `app/commissions/services.py::calculate_commission()`, unchanged from Phase 9.5A) — never an accepted request input for those specific fields.

## Test coverage

`tests/test_phase9_5d_financial_calculator.py` — 40 tests, table-driven: line calculation (simple, own-discount, price-override), boundary rejection (zero/negative quantity, discount-exceeds-gross, negative price, NaN/Infinity/-Infinity), document-discount allocation (even split, deterministic remainder with awkward per-line prices, repeatability-for-same-input, exceeding-eligible-total rejection), full document calculation (multi-line with discount+tax, awkward prices that don't divide evenly), a price-snapshot-immutability structural proof (calling the module twice with the same `LineInput` always reproduces the same result — the module has no catalog dependency at all, so a live catalog price change cannot retroactively alter it), refund/allocation validation boundaries, invoice-status resolution (VOID/DRAFT never overridden, zero/partial/full/over-payment), currency validation.
