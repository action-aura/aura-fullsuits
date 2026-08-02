# Phase 9.5D — Milestone 21: Audit Events Coverage Pass

Per the execution plan: `audit_record()` calls were wired incrementally alongside every service function throughout Milestones 3–19 (Non-Negotiable Principle 18), matching every other Owner domain's established pattern. Milestone 21 is the *completeness verification* pass.

## Verification method

An AST-based sweep (not a text grep, to correctly scope "inside this function's body") over every function definition in `app/commercial_sales/*.py` and `app/commissions/*.py`: for each function whose body contains `db_session.commit()` (a real, committed mutation), verify `audit_record(` also appears somewhere in that same function body. A function that mutates and commits without ever calling `audit_record()` is a real coverage gap — this is the same class of check `_check_crm_domain_integrity`-style preflight functions run defensively in production, applied here proactively at the source level instead.

## The one real gap found and closed

`app/commercial_sales/lead_quote_boundary.py::resolve_customer_for_accepted_quote()` set `Quote.customer_id`/`Quote.version` and committed, with no audit entry of its own. This was easy to miss because the function *does* call a canonical service (`app/leads/conversion.py::convert()`) that itself calls `audit_record()` internally — but that inner call only documents the Lead-to-Customer conversion; it has no way to know about, or record, the *separate* consequence this function applies afterward (linking the Quote to the resulting Customer). Fixed by adding a new `QUOTE_LINKED_TO_CONVERTED_CUSTOMER` action code, proven by a new assertion in `tests/test_phase9_5d_lead_quote_boundary.py::test_lead_based_quote_converts_on_acceptance` that queries `AuditLog` directly for exactly one matching row.

## Full action-code inventory (35 codes, all confirmed reachable from real code, not aspirational)

Quotes: `QUOTE_CREATED`, `QUOTE_LINE_ADDED`, `QUOTE_LINE_REMOVED`, `QUOTE_SUBMITTED`, `QUOTE_CANCELLED`, `QUOTE_EXPIRED`, `QUOTE_LINKED_TO_CONVERTED_CUSTOMER` (new this milestone). Approvals: `COMMERCIAL_APPROVAL_REQUESTED`, `COMMERCIAL_APPROVAL_APPROVED`, `COMMERCIAL_APPROVAL_CANCELLED`. Orders: `ORDER_CREATED`, `ORDER_CONFIRMED`, `ORDER_CANCELLED`. Invoices: `INVOICE_CREATED`, `INVOICE_ISSUED`, `INVOICE_VOIDED`. Payments: `PAYMENT_CONFIRMED`, `PAYMENT_REJECTED`. Allocations: `PAYMENT_ALLOCATED`, `PAYMENT_ALLOCATION_REVERSED`. Refunds: `REFUND_REQUESTED`, `REFUND_APPROVED`, `REFUND_CONFIRMED`, `REFUND_VOIDED`. Fulfillment: `FULFILLMENT_STARTED`, `FULFILLMENT_COMPLETED`, `FULFILLMENT_FAILED`, `ENTITLEMENT_CONSEQUENCE_APPLIED`. Commission policy: `COMMISSION_PLAN_CREATED`, `COMMISSION_RULE_VERSION_CREATED`, `EMPLOYEE_COMMISSION_PLAN_ASSIGNED`. Commission ledger: `COMMISSION_EARNED`, `COMMISSION_APPROVED`, `COMMISSION_REVERSED`, `COMMISSION_PAID`, `COMMISSION_PAYOUT_BATCH_CREATED`, `COMMISSION_PAYOUT_BATCH_APPROVED`.

The 4 codes Phase 9.5A had marked **(reserved)** in `docs/owner/phase9_5a/audit-event-catalog.md` (`COMMISSION_EARNED`/`COMMISSION_APPROVED`/`COMMISSION_PAID`/`COMMISSION_REVERSED`, reserved for "future `CommissionEligibilityService`") are now real and in active use via Milestone 15's `app/commissions/ledger.py` — the reservation is closed.

## Test coverage

The AST sweep itself is not (yet) a standing pytest test — run once as a point-in-time verification, matching the "structural verification stays in the test suite" precedent only where the finding warrants a permanent regression guard. Given only one gap was found and it now has its own explicit `AuditLog` query assertion, a generic "every commit has an audit call" meta-test was judged not worth the added maintenance surface for a single-domain sweep; if a second gap surfaces in a future phase, promoting the sweep to a real test at that point is the right call.
