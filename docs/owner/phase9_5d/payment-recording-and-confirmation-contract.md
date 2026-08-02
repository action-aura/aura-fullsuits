# Phase 9.5D — Milestone 10: Payment Recording and Confirmation

`app/commercial_sales/payments.py`, built on top of the existing `PaymentRecord` storage (`app/subscriptions/services.py::record_payment`/`correct_payment`, Milestone 1's audit: FULLY IMPLEMENTED AND REUSABLE) — the real, previously-missing piece is confirmation orchestration: `payments.confirm` was a seeded, FINANCE-assigned permission with zero code checking it anywhere.

## Functions

- `submit_payment(*, customer_id, amount, currency, method, payment_date, commercial_invoice_id=None, reference=None, actor_staff_user_id) -> PaymentRecord` — thin wrapper over `record_payment()`, status always starts `PENDING`. Rejects non-positive amount, invalid currency, invalid method (`("CASH", "BANK_TRANSFER", "CARD_OFFLINE", "CHEQUE", "OTHER")` — no online card processing implied, per scope).
- `confirm_payment(payment, *, actor_staff_user_id, note=None) -> PaymentRecord` — the real orchestration. Requires `status == "PENDING"`; rejects an already-`CONFIRMED` payment (`PAYMENT_ALREADY_CONFIRMED`, not a silent no-op replay). Calls the existing `correct_payment()` to perform the actual status mutation — never duplicates its logic or bypasses its `PaymentCorrectionHistory` audit trail.
- `reject_payment(payment, *, reason, actor_staff_user_id) -> PaymentRecord` — `PENDING`→`FAILED`, reason required.

## Maker-checker (Non-Negotiable Rule 4's real teeth)

`confirm_payment()` rejects `SELF_CONFIRMATION_FORBIDDEN` if `payment.recorded_by_staff_user_id == actor_staff_user_id` — checked in the service layer regardless of whether the caller's `payments.confirm` permission grant alone would have allowed it, matching the exact same "UI hiding is not authorization" principle already applied to `CommercialApproval`'s self-approval block (Milestone 6). A Finance-role holder can confirm any *other* employee's submitted payment, never their own.

## What confirmation does NOT do yet (honest, named forward reference)

Per `docs/owner/phase9_5a/commission-domain-design.md`'s existing lifecycle ("Payment confirmed → eligibility evaluated"), confirmation should eventually trigger commission-eligibility evaluation. It does not yet — Milestone 15 (commission ledger) doesn't exist at this point in the phase, and calling a function that doesn't exist would be worse than an honestly-absent forward reference. The exact integration point is marked with a comment in `confirm_payment()`; Milestone 15 adds one call, nothing else changes.

## Test coverage

`tests/test_phase9_5d_payments.py` — 9 tests: submission creates a `PENDING` record, rejects non-positive amount, rejects invalid method, confirmation by a different staff member succeeds, self-confirmation forbidden, double-confirmation rejected, rejection requires a reason, rejection succeeds, and confirmation is proven to still write a real `PaymentCorrectionHistory` row (the existing audit mechanism, not bypassed by this new orchestration layer).
