# Phase 9.5D — Milestone 28: Blocking Commercial Preflight

Per Milestone 1's own audit plan (`commercial-authority-reuse-matrix.md` item 35): "Add `_check_commercial_sales_domain_integrity` following `_check_crm_domain_integrity`'s template." `app/commercial_ops/preflight.py::_check_commercial_sales_domain_integrity()` — wired into `run_preflight()` (`flask commercial preflight`) as a **blocking** check, alongside the existing signing-key/permission-seed/i18n/CRM checks.

## What it checks

- **Canonical status enums** for all 6 new document/entry types (`Quote`, `SalesOrder`, `CommercialInvoice`, `CommercialRefund`, `CommercialApproval`, `CommissionLedgerEntry`) — every row's `status` must be a member of its own model-defined status tuple. Every one of these transitions is already guarded in the service layer (Milestones 5/8/9/12/6/15's own `_check_transition()` functions); this check exists to catch a bypass (a direct DB write, a bug in a future migration), not to duplicate the service-layer guarantee.
- **`quote_has_customer_or_lead`** — every Quote references a Customer and/or a Lead (Non-Negotiable Rule 13). This is a defense-in-depth backstop; the real, primary guarantee is the DB's own `ck_owner_quotes_at_least_one_of_customer_lead` CHECK constraint (Milestone 7), proven directly in `test_quote_with_neither_customer_nor_lead_is_rejected_at_the_db_level` — an orphan row can never even be committed, so this preflight branch is expected to never actually fire in a healthy database.
- **Two financial invariants**, the newest and most load-bearing additions: no `CommercialInvoice` with confirmed `PaymentAllocation` total exceeding its own `total`, and no `CommercialInvoice` with confirmed refunds exceeding what was actually collected (`confirmed_allocated_amount()`). The second is the exact same invariant Milestone 24's `confirm_refund()` fix enforces at the service layer (lock + re-validate before the `PAID` transition) — this preflight check is the production backstop for that same guarantee, catching it even if some future code path bypassed `confirm_refund()` entirely.

## Test coverage

`tests/test_phase9_5d_commercial_preflight.py` — 3 tests: the check passes clean (9/9 `OK`) against the real dev DB by default; a direct-DB-write bypass of the Quote status guard is caught (`Quote(status="NOT_A_REAL_STATUS")` committed directly, then `_check_commercial_sales_domain_integrity()` correctly reports `FAIL` with the offending value named); and the DB-level CHECK constraint proof for the customer-or-lead invariant.
