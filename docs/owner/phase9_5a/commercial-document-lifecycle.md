# Phase 9.5A Milestone 11 — Commercial Document Lifecycle

## New models (`owner/app/models/commercial_sales.py`)

```
quotes: id, customer_id (FK), created_by_employee_profile_id, status (DRAFT|SENT|ACCEPTED|REJECTED|
  EXPIRED|CANCELLED, default DRAFT), quote_number (unique, sequential per year e.g. "Q-2026-0001"),
  currency, subtotal/discount_total/total (Numeric, server-computed from lines, never stored as
  client input), valid_until (date, nullable), notes, created_at, updated_at, version, sent_at,
  accepted_at, rejected_at, cancelled_at

quote_lines: id, quote_id (FK), plan_id/addon_id (nullable, exactly one via CHECK), price_version_id,
  description, quantity, unit_price, overridden_unit_price (nullable), override_reason (nullable),
  discount_amount (nullable), line_total, sort_order

sales_orders: id, quote_id (FK, nullable -- an order need not originate from a quote per the "not
  every sale must require every document" allowance), customer_id, created_by_employee_profile_id,
  status (DRAFT|CONFIRMED|CANCELLED|FULFILLED), order_number (unique), currency, total, created_at,
  updated_at, version, confirmed_at, fulfilled_at, cancelled_at

sales_order_lines: same shape as quote_lines, order_id FK instead

commercial_invoices: id, sales_order_id (FK, nullable -- same simplified-flow allowance), customer_id,
  created_by_employee_profile_id, status (DRAFT|ISSUED|PARTIALLY_PAID|PAID|VOID|REFUNDED|
  PARTIALLY_REFUNDED), invoice_number (unique), currency, subtotal/discount_total/tax_total/total
  (Numeric), issued_at (nullable), due_date (nullable), created_at, updated_at, version

commercial_invoice_lines: same shape, invoice_id FK

commercial_refunds: id, commercial_invoice_id (FK), payment_record_id (FK, nullable -- which payment
  is being refunded), amount (Numeric), currency, reason, status (DRAFT|APPROVED|PAID|VOID),
  created_by_employee_profile_id, approved_by_staff_user_id (nullable), created_at, updated_at
```

## Canonical flow (server-enforced transition table, matches the spec exactly)

```
Quote(DRAFT) -> SENT -> ACCEPTED -> [SalesOrder created]
                     -> REJECTED / EXPIRED / CANCELLED (terminal)

SalesOrder(DRAFT) -> CONFIRMED -> [CommercialInvoice created] -> FULFILLED
                  -> CANCELLED (terminal)

CommercialInvoice(DRAFT) -> ISSUED -> PARTIALLY_PAID -> PAID
                                   -> VOID (terminal, only from DRAFT/ISSUED with no payments)
                          -> REFUNDED / PARTIALLY_REFUNDED (from PAID/PARTIALLY_PAID via CommercialRefund)
```

Simplified flow allowed (per the spec's own "not every sale must require every document"): a
`CommercialInvoice` may be created with `sales_order_id = NULL` directly against a `Customer`, for a
simple/manual sale — but the state machine itself, and the requirement that a transition is always an
explicit, audited service call, is unchanged regardless of which document chain was used.

## Reused, not duplicated: `PaymentRecord`

`PaymentRecord` (existing, `owner/app/models/subscriptions.py`) gets one new nullable column,
`commercial_invoice_id` (FK) — additive, does not touch its existing `subscription_id`-based usage.
Confirming a payment against an invoice is: create/find a `PaymentRecord` row, set
`commercial_invoice_id`, transition it to the existing `VERIFIED`/confirmed status (real, existing
service function, not rebuilt) — the invoice's own `status` (PARTIALLY_PAID/PAID) is derived from the
sum of its confirmed `PaymentRecord` rows, computed at read time, never a second source of truth stored
redundantly on the invoice.

## Server-side rules (all Non-Negotiable Principle 5/8-driven)

- `employee may create draft quote/order/invoice only with permission` — `quotes.create`/
  `orders.create`/`invoices.create`, granted to SALES by default (matches existing SALES scope).
- `employee cannot confirm payment without finance/admin permission` — `payments.confirm` (existing
  permission, already NOT granted to SALES per Milestone 1's audit — reused unchanged).
- Totals always `Decimal`, currency always explicit on every document and every line.
- Idempotency keys required for `invoices.issue`/`payments.confirm`/`refunds.create` — same real
  pattern as `issue_license_key()`'s own idempotency ledger, reused, not reinvented.
- Concurrent confirmations safe — optimistic `version` column on every mutable document, `VERSION_CONFLICT`
  error (Milestone 19) on a stale write.
- No direct historical amount rewrite — once `ISSUED`, an invoice's lines/totals are immutable
  (Price Authority Rule 2); correction is always a new `CommercialRefund` or a voided-and-reissued
  document, never an `UPDATE` of a posted total.

## Explicitly not claimed

"These documents are not described as legally compliant tax invoices" — no tax-authority sequential
numbering scheme, no e-invoicing XML/QR generation, no fiscal registration is implemented or implied
this phase; `invoice_number` is an internal reference only.
