# Phase 9.5D — Milestone 2: Canonical Commercial Funnel Contract

Built on top of Phase 9.5A's existing design (`docs/owner/phase9_5a/commercial-document-lifecycle.md`, `price-authority-rules.md`, `payment-and-fulfillment-contract.md`, `commission-domain-design.md`, `commission-calculation-contract.md`, `commission-rbac-rules.md`) — not re-derived. Where 9.5A already specified a rule, it's cited, not restated differently. Where the current spec (Milestone 2's explicit per-step field list) asks for more precision than 9.5A recorded, that precision is added here.

## The funnel

```
QUALIFIED LEAD OR CUSTOMER
  -> DRAFT QUOTE
  -> SENT QUOTE (spec's "SUBMITTED"; 9.5A's schema already names this state SENT — reused, not renamed)
  -> [PENDING_APPROVAL if a pricing/discount exception applies -- Milestone 6, layered on top of SENT,
      not a distinct Quote.status value; see "Approval is orthogonal to Quote.status" below]
  -> ACCEPTED (by Customer) or REJECTED / EXPIRED / CANCELLED (terminal, no Order)
  -> [Lead->Customer conversion via the canonical Phase 9.5C service, if the Quote was Lead-based --
      Milestone 7]
  -> SalesOrder(DRAFT) -> CONFIRMED
  -> CommercialInvoice(DRAFT) -> ISSUED
  -> PaymentRecord created (recorded) -> confirmed (payments.confirm)
  -> Payment allocated to the Invoice (Milestone 11 -- genuinely new, see note below)
  -> Invoice status recomputed from confirmed+allocated payment sum: ISSUED -> PARTIALLY_PAID -> PAID
     (formula: docs/owner/phase9_5a/payment-and-fulfillment-contract.md's resolve_invoice_status(),
      extended to sum allocations rather than raw confirmed PaymentRecord.commercial_invoice_id matches
      -- see Milestone 11 note)
  -> Fulfillment eligibility check (Milestone 13) -> Subscription created/renewed via the existing
     canonical service, License issued via issue_license_key() -> SalesOrder.status = FULFILLED
  -> Commission eligibility evaluated on payment confirmation (not on fulfillment) -> ledger entry
     EARNED (commission-domain-design.md's lifecycle, unchanged)
  -> Commission APPROVED (commissions.approve) -> PAID (via commission_payout_batch)
```

## Quote status naming: reconciling the two specs

The current spec's milestone list uses `SUBMITTED`/`PENDING_APPROVAL`/`APPROVED`/`PRESENTED`. The **actual, already-migrated** `QUOTE_STATUSES` enum (`app/models/commercial_sales.py:21`) is `(DRAFT, SENT, ACCEPTED, REJECTED, EXPIRED, CANCELLED)` — 6 values, not 9. Per Non-Negotiable Rule "do not create a second Quote/pricing authority" and the entry gate's explicit instruction to prefer the existing Phase 9.5A schema, **the existing 6-value enum is authoritative and is not widened**. The mapping:

| Current spec's term | Real `Quote.status` value | How it's represented |
|---|---|---|
| DRAFT | `DRAFT` | direct |
| SUBMITTED | `SENT` | direct (existing name reused) |
| PENDING_APPROVAL | `SENT` + an open `CommercialApproval` row referencing this Quote | approval is a separate record, not a Quote status (see below) |
| APPROVED | `SENT` + a resolved-APPROVED `CommercialApproval` row | same |
| REJECTED_INTERNAL | `SENT` + a resolved-REJECTED `CommercialApproval` row | same |
| PRESENTED | `SENT` (no distinct "presented to customer" state in the existing schema — presentation is a UI/workflow action, not a persisted state transition) | Milestone 19 UI concern only |
| ACCEPTED | `ACCEPTED` | direct |
| REJECTED_BY_CUSTOMER | `REJECTED` | direct |
| EXPIRED | `EXPIRED` | direct |
| CANCELLED | `CANCELLED` | direct |
| SUPERSEDED | not modeled — a superseding Quote is a new `Quote` row; the old one is `CANCELLED` with a note referencing the new quote's number | no schema change needed |

**Approval is orthogonal to `Quote.status`, not a distinct status value.** This follows the audit's Milestone 1 finding directly: the established codebase convention (RenewalRequest, PendingActivation, PilotRecord) is a document-specific status enum *plus* a separate approval/decision record, not folding approval states into the parent's own enum. A new `CommercialApproval` model (Milestone 6) references the Quote (or any other approval-triggering target) by UUID and carries its own PENDING/APPROVED/REJECTED/CANCELLED/EXPIRED lifecycle, independent of `Quote.status`.

## Full per-step contract

| Step | Source state | Target state | Permission | Ownership requirement | Approval requirement | Reason requirement | Version check | Idempotency | Audit action | Reversible? | Downstream side effects | Forbidden side effects |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Create Quote | (none) | `Quote.DRAFT` | `quotes.create` | actor becomes `created_by_employee_profile_id`; Lead/Customer must be visible to actor per existing CRM ownership rules | none | none | n/a (new row) | `QUOTE_CREATE` | `QUOTE_CREATED` | n/a | none | no Order/Invoice/Payment/Subscription/License/Commission |
| Edit Quote lines | `DRAFT` | `DRAFT` | `quotes.create` (creator) or `quotes.approve` (management) | same as create | none unless line triggers exception (Milestone 6) | none | required | n/a (mutation, not a new document) | `QUOTE_LINE_ADDED`/`UPDATED`/`REMOVED` | yes (still DRAFT) | recomputes subtotal/discount_total/total via Milestone 3's calc service | no total is ever client-supplied |
| Submit Quote | `DRAFT` | `SENT` | `quotes.create` (own) or `quotes.approve` (management, any) | ownership check | **if** any line/document exception exists, a `CommercialApproval` row is created PENDING and must resolve APPROVED before... | none to submit; reason required per-exception (Milestone 6) | required | `QUOTE_SUBMIT` | `QUOTE_SUBMITTED` | no (locks lines against free edit; see "material revision" below) | creates `CommercialApproval` row(s) if exceptions exist | none |
| Approve/reject exception | `CommercialApproval.PENDING` | `APPROVED`/`REJECTED` | `quotes.approve`/`pricing.override`-holder per exception type (Milestone 6) | requester != approver (self-approval block, Non-Negotiable 12) | is the approval | required on reject | bound to exact Quote version at request time; stale if Quote changed since | `APPROVAL_DECIDE` | `QUOTE_APPROVAL_APPROVED`/`REJECTED` | no (immutable decision) | none directly; a Quote with an unresolved/rejected required approval cannot progress to Order | approval never itself mutates Quote lines/totals |
| Record customer decision | `SENT` (all required approvals resolved APPROVED) | `ACCEPTED`/`REJECTED` | `quotes.record_customer_decision` | ownership check | n/a | required on reject, recommended on accept | required | `QUOTE_CUSTOMER_DECISION` | `QUOTE_ACCEPTED`/`QUOTE_REJECTED_BY_CUSTOMER` | no (terminal for REJECTED; ACCEPTED proceeds) | **on ACCEPTED with a Lead-based Quote**: triggers Milestone 7's Lead-to-Customer conversion boundary before any Order may be created | acceptance alone creates no Payment/Subscription/License/Commission (Non-Negotiable 1) |
| Quote expiry | `SENT` | `EXPIRED` | system (server-evaluated against `valid_until`), no actor | n/a | n/a | n/a | n/a | `QUOTE_EXPIRE` (system) | `QUOTE_EXPIRED` | no | none | an `ACCEPTED` Quote can never silently expire (evaluated only on still-`SENT` quotes) |
| Cancel Quote | `DRAFT`/`SENT` | `CANCELLED` | `quotes.create` (own) or `quotes.approve` (any) | ownership check | none | required | required | `QUOTE_CANCEL` | `QUOTE_CANCELLED` | no | none | cannot cancel an `ACCEPTED` Quote (use Order cancellation instead) |
| Create Order from Quote | `Quote.ACCEPTED` + confirmed Customer | `SalesOrder.DRAFT` | `orders.create` | actor's ownership of the Customer; Quote must belong to the same Customer | none | none | Quote version re-checked (must not have changed since acceptance) | `ORDER_CREATE_FROM_QUOTE`, keyed to the Quote's own idempotency so re-submitting the same accepted Quote never creates two Orders | `ORDER_CREATED` | n/a | snapshots Quote lines into `SalesOrderLine` rows | no Invoice/Payment/Fulfillment yet (Non-Negotiable 2) |
| Confirm Order | `DRAFT` | `CONFIRMED` | `orders.approve` | ownership check | none (Order confirmation is not itself a pricing exception; any exception was already resolved at Quote stage) | none | required | `ORDER_CONFIRM` | `ORDER_CONFIRMED` | no | none | confirming does not mean paid (Non-Negotiable 2) |
| Cancel Order | `DRAFT`/`CONFIRMED` (not yet `FULFILLED`) | `CANCELLED` | `orders.approve` | ownership check | none | required | required | `ORDER_CANCEL` | `ORDER_CANCELLED` | no | if an Invoice was issued against it, that Invoice must itself be voided separately, never implicitly | cannot cancel a `FULFILLED` order (use Refund) |
| Create Invoice from Order | `SalesOrder.CONFIRMED` | `CommercialInvoice.DRAFT` | `invoices.create` | ownership check | none | none | Order version re-checked | `INVOICE_CREATE_FROM_ORDER` | `INVOICE_CREATED` | n/a | snapshots Order lines into `CommercialInvoiceItem` rows | no statutory/fiscal claim (Non-Negotiable 15) |
| Issue Invoice | `DRAFT` | `ISSUED` | `invoices.issue` | ownership check | none | none | required | `INVOICE_ISSUE` | `INVOICE_ISSUED` | no (lines become immutable — Price Authority Rule 2) | sets `due_date` if not already set | issuing is not payment confirmation (Non-Negotiable 3) |
| Void Invoice | `DRAFT`/`ISSUED` with zero confirmed payments | `VOID` | `invoices.issue` + recent-authentication | ownership check | none | required | required | `INVOICE_VOID` | `INVOICE_VOIDED` | no | none | a Invoice with any confirmed payment cannot be silently voided (use Refund) |
| Record Payment | (customer paid, employee submits) | `PaymentRecord` created, status per existing `PAYMENT_STATUSES` | `payments.create` | ownership check on the target Customer/Invoice | none | none | n/a (new row) | required (existing `record_payment()` idempotency, if any — verify in Milestone 10) | `PAYMENT_RECORDED` | n/a | none yet | recording is not confirming (Non-Negotiable 4) |
| Confirm Payment | recorded | confirmed | `payments.confirm` | requester != confirmer if maker-checker policy applies (Milestone 10) | is itself the confirming action | none | required | `PAYMENT_CONFIRM` | `PAYMENT_CONFIRMED` | no | eligible for allocation (Milestone 11); triggers commission eligibility evaluation (per `commission-domain-design.md`'s lifecycle) | never triggers fulfillment directly — fulfillment re-checks eligibility independently (Milestone 13) |
| Allocate Payment | confirmed, unallocated (fully or partially) | allocation record created | `payments.allocate` | ownership check on both Payment and Invoice | none | none | required, concurrency-protected | required | `PAYMENT_ALLOCATED` | reversible via a dedicated reversal action, requires authority+reason | recomputes Invoice status (ISSUED/PARTIALLY_PAID/PAID) | cross-currency allocation forbidden; cannot exceed unallocated Payment or Invoice outstanding |
| Fulfill Order | `CONFIRMED` Order, Invoice `PAID`/`PARTIALLY_PAID` per policy, no blocking refund | `FULFILLED` | `fulfillment.execute` | ownership check | required approval (if any) already resolved | none | required | required, checked against "no previous successful fulfillment for this line" | `FULFILLMENT_STARTED`/`COMPLETED`/`FAILED` | no (but retry-safe on failure) | calls existing Subscription create/renew pipeline + `issue_license_key()` (never direct row writes) | never creates Installation (separate device-activation event) |
| Commission earn | Payment confirmed (per `commission-domain-design.md`, unchanged from 9.5A) | ledger entry `EARNED` | system-triggered (not a direct user action) | n/a | n/a | n/a | n/a | `source_payment_record_id` partial-unique-index (existing) prevents duplicate earning | `COMMISSION_EARNED` | reversible via a new REVERSAL row, never an edit | none | never computed from Quote/Order/Invoice totals, tax, or unconfirmed payment (Non-Negotiable 6) |
| Commission approve | `EARNED` | `APPROVED` | `commissions.approve` | requester's employee profile != entry's own `employee_profile_id` (self-approval block, existing design) | is itself the approval | none | required | required | `COMMISSION_APPROVED` | no | none | never SALES-grantable (existing RBAC) |
| Commission payout | `APPROVED` (batched) | `PAID` | `commissions.pay` | n/a (batch-level) | none beyond the batch's own approval | none | required | required | `COMMISSION_PAID` | no | records external payout reference only — no bank integration | never touches `PaymentRecord`/`CommercialInvoice` |
| Commission reverse | `EARNED`/`APPROVED`/`PAID` (on refund of the source payment) | new `REVERSED`-type ledger row | `commissions.reverse` | n/a | none (system or Finance-triggered from Refund confirmation) | required (references the triggering Refund) | n/a (append-only) | required | `COMMISSION_REVERSED` | n/a (a reversal is itself terminal) | none | never edits the original EARNED/APPROVED/PAID row |

## Reconciling Milestone 11 (Payment Allocation) with the existing 9.5A design

`docs/owner/phase9_5a/payment-and-fulfillment-contract.md` describes Invoice status as derived from "the sum of its confirmed `PaymentRecord` rows" via `PaymentRecord.commercial_invoice_id` — an implicit 1-payment-to-1-invoice model. The current spec's Milestone 11 explicitly wants a **many-to-many allocation model**: one confirmed Payment may be split across multiple outstanding Invoices, and one Invoice may receive multiple Payments. This is a genuine, acknowledged extension beyond 9.5A's original design, not a contradiction of it:

- `PaymentRecord.commercial_invoice_id` (existing, nullable FK) remains valid for the simple case (one payment fully allocated to one invoice) and continues to work unchanged for pre-9.5D usage (subscription-only payments never touch invoices at all).
- A new `PaymentAllocation` model (Milestone 11) becomes the authoritative source for Invoice status computation *whenever any allocation row exists for that invoice* — `resolve_invoice_status()` is extended to sum `PaymentAllocation.allocated_amount` rather than raw `PaymentRecord` amounts, once this milestone lands. This is a real, additive schema change (a new table, not a modification of `PaymentRecord`'s existing usage), consistent with "additive-only migrations for proven gaps" (Milestone 22 principle, carried from 9.5C).

## What this milestone explicitly does not change

`Quote`/`SalesOrder`/`CommercialInvoice`/`CommercialRefund`/`CommissionLedgerEntry` schemas are used exactly as migrated in Phase 9.5A — no column added or renamed by this milestone. `PaymentAllocation` (Milestone 11) and `CommercialApproval` (Milestone 6) are the two genuinely new models this phase introduces, both already anticipated by Milestone 1's audit as "REQUIRES NEW AUTHORITY."
