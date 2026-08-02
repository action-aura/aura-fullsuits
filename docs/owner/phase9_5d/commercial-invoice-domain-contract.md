# Phase 9.5D — Milestone 9: Commercial Invoice Domain Contract

`app/commercial_sales/invoices.py`, built directly on the existing `CommercialInvoice`/`CommercialInvoiceItem` schema (Phase 9.5A) — no new model, no migration this milestone.

## Functions

- `create_invoice_from_order(order, *, actor_employee_profile_id, actor_staff_user_id, idempotency_key, due_date=None) -> CommercialInvoice` — requires `order.status == "CONFIRMED"`. Snapshots every `SalesOrderLine` into a new `CommercialInvoiceItem`. One active (non-`VOID`) Invoice per Order, enforced explicitly. Idempotent via the shared `CommercialOperationsIdempotencyKey` ledger (`OPERATION_CODE = "INVOICE_CREATE_FROM_ORDER"`).
- `issue_invoice(invoice, ...)` — `DRAFT`→`ISSUED`, sets `issued_at`; defaults `due_date` to +30 days if not already set at creation.
- `confirmed_allocated_amount(invoice) -> Decimal` — sums non-reversed `PaymentAllocation` rows against the invoice. This is Milestone 11's authoritative "has this invoice received any money" query, used here to gate voiding. Correctly returns `0` before Milestone 10/11's services exist (no allocation can be created without a confirmed Payment) — not a placeholder, the genuinely correct answer for a fresh invoice.
- `void_invoice(invoice, *, reason, ...)` — `DRAFT`/`ISSUED`→`VOID`, only if `confirmed_allocated_amount(invoice) == 0`; reason required. A paid invoice cannot be silently voided (Non-Negotiable Rule 16/Milestone 12's job — use a `CommercialRefund` instead).

## Explicitly not a statutory document (Non-Negotiable Rule 15)

No tax-registration number, fiscal QR/UUID, or e-invoicing submission-status field exists on the model — verified structurally by `test_no_statutory_claim_fields_present`, which introspects `CommercialInvoice.__table__.columns` directly rather than relying on documentation alone. `invoice_number` is an internal reference only.

## Ownership

`apply_ownership_filter()` extended with a `CommercialInvoice` branch — creator-only, same rule as `Quote`/`SalesOrder`.

## Test coverage

`tests/test_phase9_5d_invoices.py` — 8 tests: creation from a confirmed Order, rejection when the Order isn't confirmed, issue sets the default due date, void with zero payments, void-requires-reason, a paid invoice cannot be voided (a real `PaymentAllocation` row constructed directly against the Milestone-11 schema, since M10/M11's own services don't exist yet at this point in the phase), idempotent replay, and the statutory-claim column-introspection proof.
