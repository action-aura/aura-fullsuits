# Aura Clinic — Financial Correctness Audit

## INVOICES

`products/clinic/backend/api/clinic_api.py::create_invoice()` (line 672-737) —
**materially better financial-integrity design than Retail's `create_sale()`**:

- `subtotal = sum(qty * unit_price for items)` (line 696) is **computed server-side
  from line items**, not trusted from a client-submitted subtotal field. **PROVEN
  by direct read** — this is the one place in either product where the server
  does not blindly trust a client-sent aggregate.
- `discount` is explicitly clamped: `if discount < 0: discount = 0` /
  `if discount > subtotal: discount = subtotal` (lines 699-700) — negative and
  over-subtotal discounts are impossible. **This directly contradicts Retail's
  equivalent gap** (see `03`, `05`).
- Tax is computed **tax-after-discount**, matching Retail's default and explicitly
  commented as intentional: `"Discount applied before tax (tax-after-discount),
  mirroring the POS fix"` (line 697) — confirms this was a deliberate, documented
  design decision to keep Clinic and Retail's tax policy consistent, and it is
  correctly implemented: `taxable = subtotal - discount; tax = taxable * rate`
  (lines 701-703).
- **Zero-value invoice**: possible (`items=[]` → `subtotal=0` → `total=0`), not
  explicitly rejected, but not harmful (a $0 invoice is a no-op, not a loss).
- **Negative values**: `unit_price`/`qty` per item are not validated for sign —
  same class of gap as Retail's line-item validation gap (`03`), not fixed here
  either. A negative `qty` or `unit_price` line item would silently produce a
  negative `subtotal`/`total`. **Not covered by any existing test** (see `02`).
- **Duplicate invoice submission**: `inv_no = f'INV-C-{int(time.time())}-{random.randint(100,999)}'`
  (line 692) — time+random, not a true atomic per-company sequence, and **no
  idempotency-key mechanism** exists on this route (unlike Retail's `create_sale`,
  which has one). A double-submitted request (network retry, accidental double-tap)
  creates two separate, real invoices with two different `invoice_number`s — there
  is nothing to detect or collapse the duplicate. P2.
- **Editing/cancellation/archival**: no `PUT`/`DELETE` route for invoices was found
  in the section read; not confirmed present or absent beyond this excerpt —
  UNVERIFIED, flagged for a follow-up pass rather than asserted either way.
- **A vestigial, always-failing cross-subsystem call**: lines 723-745 attempt to
  `from database.subsystem_db import sub_create, get_accounting_conn` and mirror
  the invoice into an "Accounting" subsystem, wrapped in a bare
  `try: ... except Exception: pass`. **Confirmed by repo-wide search**: neither
  `database/subsystem_db.py` nor any `get_accounting_conn` definition exists
  anywhere in `aura-fullsuits` — this import fails on every single invoice
  creation and is silently swallowed. The code comment ("linked to both invoice
  systems... appears in clinic billing AND Accounting") describes behavior that
  **cannot occur** in the extracted, standalone Clinic product — it is a leftover
  reference to the original monolith's cross-subsystem integration. Not a
  financial-correctness defect (the clinic invoice itself is written correctly
  regardless), but a real "silently-failing dead code path with a misleading
  comment and zero operator visibility" finding. P4.

## PAYMENTS

`record_payment()` (`clinic_api.py` line 785-808) — **materially weaker than
invoice creation, and weaker than Retail's `create_sale` in specific ways**:

- **Tenant isolation is correctly enforced**: `_owned(conn, 'clinic_invoices', ...)`
  (line 794) — this is the exact IDOR gap Phase 3 fixed; re-confirmed present and
  correct here.
- **No amount validation whatsoever**: `data.get('amount')` (line 799) is inserted
  directly as `amount_paid` with **no `float()` coercion, no sign check, no zero
  check**. **PROVEN**: a negative `amount` is accepted and stored as-is; a
  malformed/non-numeric `amount` would raise a raw SQLite type error (uncaught —
  no `try/except` wraps the insert), producing a 500 rather than a clean 400.
- **No overpayment cap**: `status = 'paid' if inv and paid >= inv['total'] else 'partial'`
  (line 803) — an overpayment (e.g. paying $500 against a $100 invoice) is accepted,
  marks the invoice `'paid'`, and the excess is simply not tracked anywhere as
  credit or refund-owed. Compare to Retail's `create_sale`, which at least computes
  `change = max(0.0, paid - total)` for a single-transaction overpayment; Clinic's
  multi-payment model has no equivalent concept at all for "this payment overshot."
- **No idempotency protection**: unlike `create_sale`, `record_payment` has no
  idempotency-key mechanism. A double-submitted payment (retry, double-tap)
  inserts two `clinic_payments` rows, and the running `SUM(amount_paid)` (line 802)
  would then double-count it — the invoice could be marked `'paid'` after only
  half the real amount was actually collected being duplicated, or a genuine
  overpayment gets recorded as if the patient paid twice. **PROVEN by direct
  read** (no code path prevents this); not covered by any test (`02`).
- **No payment reversal/deletion route** was found in the section read for this
  pass — UNVERIFIED whether one exists elsewhere in the file.

**Classified P1** — "duplicate payments" is explicitly listed in the audit's own
P1 example list, and this is a proven, unguarded path to exactly that, on the one
product where financial write validation is otherwise the stronger of the two.

## INVOICE STATUS

States actually found in code: `'unpaid'` (created), `'partial'`, `'paid'`
(both set by `record_payment`'s `SUM`-vs-`total` comparison). **No `'draft'`,
`'cancelled'`, or `'voided'` state was found anywhere in the routes read** — if
an invoice is created in error, there is no state to mark it as void; it would
need to be paid, ignored, or (if a delete route exists elsewhere, unconfirmed)
deleted outright with no audit trail distinction from "never happened." This is a
**missing required state**, not a broken transition — flagged as a functional
gap (P3) rather than a financial-correctness defect, since the states that do
exist transition correctly per the code read.

## EXPENSES (Lab Expenses)

Not independently re-derived from first principles in this pass beyond
confirming the existing test `clinic_workflow_test.py::test_lab_expense_entry_and_precision`
and `test_lab_expense_deletion` both **PASS** in isolation (`02`). Decimal
precision and deletion are therefore **PROVEN** covered; duplicate-entry
prevention, negative-amount rejection, and category-total reporting were **not**
independently re-examined in source in this pass — UNVERIFIED beyond what the
existing test names imply.

## REPORTING

Not independently re-derived in this pass. `clinic_workflow_test.py::test_dashboard_stats_shape_and_counts`
**PASSES** in isolation, confirming the dashboard endpoint returns a
correctly-shaped response, but this audit did not independently trace the
dashboard's SQL for date-filtering/cancelled-invoice-exclusion correctness
(no `'cancelled'` state exists to exclude, per above — so that specific concern
is moot given current states) within the time available for this pass.
UNVERIFIED beyond the existing test's assertions.

## Summary table

| Question | Answer |
|---|---|
| Invoice subtotal server-computed (not client-trusted)? | **PROVEN YES** |
| Discount clamped to [0, subtotal]? | **PROVEN YES** |
| Tax-after-discount, matching Retail's default? | **PROVEN YES, deliberate** |
| Payment amount validated (sign, zero, overpayment)? | **PROVEN NO** |
| Payment idempotency-protected? | **PROVEN NO** |
| "Mirrors into Accounting" claim in code comment true for this product? | **PROVEN FALSE** — dead, always-failing, silently-swallowed import |
| All required invoice states present? | **PROVEN NO** — no cancelled/voided/draft state |
