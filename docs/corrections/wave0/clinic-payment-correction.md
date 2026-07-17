# Wave 0 Correction — Clinic Payments (AUDIT-011/012/018)

Status: **FIXED AND VERIFIED**

## Root cause

`record_payment()` performed no amount validation at all (negative, zero,
and arbitrary overpayment were all accepted with `200`), had no
deduplication mechanism, and updated `clinic_invoices.status`/`amount_paid`
outside any transaction alongside a separate, unguarded payment insert.
`create_invoice()` similarly ran its header insert, prescription back-link
update, and line-item inserts without a shared transaction.

## What was fixed — `record_payment()`

- `amount` must parse as a `Decimal` and be `> 0` (`400` otherwise).
- The invoice's outstanding balance is computed as
  `total - SUM(existing clinic_payments.amount_paid)`, and the new payment
  is rejected (`400`) if it would exceed that outstanding balance by more
  than a half-cent rounding tolerance. This product has no customer-credit
  ledger, so overpayment is rejected outright rather than silently accepted
  as credit — there was no tested credit-ledger policy to preserve.
- A real `idempotency_key` (new company-scoped-unique DB column) makes a
  retried/double-submitted request return the original payment instead of
  creating a second one.
- The payment insert and the invoice `status`/`amount_paid` update happen
  inside one `BEGIN IMMEDIATE` transaction (AUDIT-018) — a mid-update
  failure can no longer leave a payment recorded with no matching status
  update, or vice versa.
- Only the invoice statuses the schema already supports (`unpaid`,
  `partial`, `paid`) are used — no new status was invented.

## What was fixed — `create_invoice()`

- The header insert, the prescription back-link update (previously wrapped
  in its own swallowed `try/except: pass`), and the line-item insert loop
  now run inside one `BEGIN IMMEDIATE` transaction — a failure partway
  through the line-item loop rolls back the header too, so no invoice can
  exist with zero line items due to a partial failure.

## Worked example (verified by test), $100 invoice, 0% tax, no discount

| Request | Result |
|---|---|
| `amount = 0.00` | rejected (400) |
| `amount = -10.00` | rejected (400) |
| `amount = 40.00` | accepted, invoice status → `partial` |
| `amount = 40.00` again, same `idempotency_key` | returns the **original** payment; total paid stays `40.00`, not `80.00` |
| `amount = 70.00` (after the `40.00` above) | rejected (400) — exceeds the `60.00` outstanding balance |
| `amount = 60.00` (after the `40.00` above) | accepted, invoice status → `paid` |

## Information-disclosure fix (found by automated review, fixed same wave)

Both `create_invoice()`'s and `record_payment()`'s exception handlers
originally echoed the raw exception text to the HTTP client
(`f'Could not create invoice: {e}'` / `str(e)`) — the same information
-disclosure pattern already remediated once before in `create_patient()`
(a SQL-bind error can embed request values, including patient PII, as
literals in its message). Both handlers now log server-side via
`logging.getLogger('aura.clinic').error(...)` and return a generic message.
The same automated review also found `record_payment()`'s new
`idempotency_key` lookup was unscoped by `company_id` — fixed to filter
`AND company_id=?`, and the identical pattern was proactively fixed in
Retail's `create_return()` idempotency lookup, which had just been added
with the same class of bug.

## Tests

`products/clinic/tests/clinic_payment_wave0_test.py` (9 tests): the worked
example table above, payment against a nonexistent invoice (404),
non-numeric amount rejected, and invoice-creation atomicity (a failing line
item leaves no orphaned header row).

## Commits

`8f315ab` (initial payment/invoice rewrite), `57a3048` (information
-disclosure + idempotency-scoping fixes).

## Residual risk

No customer-credit ledger exists for Clinic (or Retail's non-AR paths); an
overpayment is always rejected rather than banked as credit. If a future
wave adds such a ledger, this rejection behavior must be revisited
deliberately, not silently changed.
