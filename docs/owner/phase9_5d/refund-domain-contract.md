# Phase 9.5D — Milestone 12: Refund Domain Contract

`app/commercial_sales/refunds.py`, on the existing `CommercialRefund` schema (Phase 9.5A), extended this milestone with `version`/`approved_at`/`paid_at`/`voided_at` and indexes on `commercial_invoice_id`/`payment_record_id` (migration `f2b7d4e91a63`) — a real, proven gap: every sibling document (Quote/SalesOrder/CommercialInvoice) already had both an optimistic-lock version and a timestamp per real transition; `CommercialRefund` had neither in the original migration.

## Functions

- `total_confirmed_refunds(invoice) -> Decimal` — sum of `PAID` refund amounts against an invoice.
- `refundable_balance(invoice) -> Decimal` — `calculate_refundable_balance(confirmed_allocated_amount(invoice), total_confirmed_refunds(invoice))` (Milestone 3's calculator, unchanged). The refundable base is money **actually collected** (confirmed + allocated), never the invoice's face total — you cannot refund money that was never received.
- `create_refund(invoice, *, amount, reason, payment_record_id, ...) -> CommercialRefund` — `DRAFT`. Reason required; amount validated against `refundable_balance()` (prior refunds included — proven by `test_second_refund_respects_prior_refund`).
- `approve_refund(refund, ...) -> CommercialRefund` — `DRAFT`→`APPROVED`. Self-approval blocked: the refund's creator (resolved from `created_by_employee_profile_id` back to a `StaffUser` via the same `EmployeeProfile.staff_user_id` link Phase 9.5C's ownership filter uses for `Customer`) cannot also approve it — enforced in the service layer regardless of permission grant.
- `confirm_refund(refund, ...) -> CommercialRefund` — `APPROVED`→`PAID`. Recomputes the parent invoice's status: `REFUNDED` if total confirmed refunds now cover everything collected, `PARTIALLY_REFUNDED` otherwise — layering on top of the payment-based status exactly as `docs/owner/phase9_5a/payment-and-fulfillment-contract.md` originally specified.
- `void_refund(refund, ...) -> CommercialRefund` — `DRAFT`/`APPROVED`→`VOID`.

## A Refund never touches the original Payment

`test_no_hard_delete_original_payment` proves `PaymentRecord.amount`/`.status` are byte-for-byte unchanged after a full refund confirms — a Refund is its own, separate, immutable-history document (Non-Negotiable Rule 16, `payment-and-fulfillment-contract.md`'s own explicit "never a negative PaymentRecord row, never an edit to the original payment" rule, unchanged from Phase 9.5A).

## Test coverage

`tests/test_phase9_5d_refunds.py` — 8 tests: full-refund lifecycle (invoice ends `REFUNDED`), partial refund (`PARTIALLY_REFUNDED`), reason-required, exceeds-refundable-balance rejection, a second refund correctly respects the first's already-confirmed amount, self-approval forbidden, void, and the original-payment-untouched proof.
