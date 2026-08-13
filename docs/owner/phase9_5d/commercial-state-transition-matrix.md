# Phase 9.5D — Milestone 2: Commercial State Transition Matrix

Raw valid-transition tables per document type, companion to `commercial-funnel-contract.md` (which carries the full per-step policy: permissions, ownership, approval, audit, side effects). This doc is the mechanical reference every service function's transition-validation code should be checked against — matching the exact style of `app/subscriptions/services.py`'s `VALID_TRANSITIONS` dict and `app/leads/services.py`'s lead-status transition table.

## Quote (`app/models/commercial_sales.py::QUOTE_STATUSES`)

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `SENT` | submit |
| `SENT` | `ACCEPTED` | customer decision (accept) |
| `SENT` | `REJECTED` | customer decision (reject) |
| `SENT` | `EXPIRED` | system, `valid_until` passed |
| `DRAFT` | `CANCELLED` | cancel |
| `SENT` | `CANCELLED` | cancel |

No transition out of `ACCEPTED`/`REJECTED`/`EXPIRED`/`CANCELLED` (all terminal for the Quote itself — a "revision" is always a new Quote row, referencing the old one, never a resurrection of a terminal Quote).

## CommercialApproval (new model, Milestone 6)

| From | To | Trigger |
|---|---|---|
| (none) | `PENDING` | exception detected at Quote submit / line edit |
| `PENDING` | `APPROVED` | approver decision |
| `PENDING` | `REJECTED` | approver decision |
| `PENDING` | `CANCELLED` | requester withdraws (e.g. edits the line to remove the exception) |
| `PENDING` | `EXPIRED` | system, if the parent Quote itself expires/cancels while unresolved |

No transition out of `APPROVED`/`REJECTED`/`CANCELLED`/`EXPIRED` — a new exception always produces a new `CommercialApproval` row (matches the audit's "requester cannot silently reuse a stale approval" requirement; a Quote modification after approval invalidates it, forcing a fresh request).

## SalesOrder (`SALES_ORDER_STATUSES`)

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `CONFIRMED` | confirm |
| `CONFIRMED` | `FULFILLED` | fulfillment orchestration success |
| `DRAFT` | `CANCELLED` | cancel |
| `CONFIRMED` | `CANCELLED` | cancel (only if not yet `FULFILLED`) |

No transition out of `FULFILLED`/`CANCELLED`.

## CommercialInvoice (`INVOICE_STATUSES`)

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `ISSUED` | issue |
| `DRAFT` | `VOID` | void (no payments possible on DRAFT) |
| `ISSUED` | `VOID` | void (only if zero confirmed payments) |
| `ISSUED` | `PARTIALLY_PAID` | payment allocation, `0 < allocated_sum < total` |
| `ISSUED` | `PAID` | payment allocation, `allocated_sum >= total` |
| `PARTIALLY_PAID` | `PAID` | further allocation |
| `PARTIALLY_PAID` | `PARTIALLY_REFUNDED` | confirmed partial refund |
| `PAID` | `PARTIALLY_REFUNDED` | confirmed partial refund |
| `PAID` | `REFUNDED` | confirmed full refund |

`resolve_invoice_status()` (existing, `payment-and-fulfillment-contract.md`) computes `ISSUED`/`PARTIALLY_PAID`/`PAID` purely from the allocated-payment sum — never a direct state-mutation call; refund states layer on top once a `CommercialRefund.status == PAID` exists. `VOID`/`DRAFT` are never overridden by payment-sum computation (existing rule, unchanged).

## PaymentRecord (existing `PAYMENT_STATUSES`, `app/subscriptions/services.py:42`, reused unchanged — Milestone 10 audits and extends usage, does not redefine the enum)

Verified exact set: `("PENDING", "CONFIRMED", "FAILED", "REFUNDED", "VOIDED")`. `confirm_payment()` (Milestone 10, new) transitions `PENDING -> CONFIRMED`; `VOIDED` is reserved for pre-confirmation cancellation (matches Non-Negotiable Principle 4's "cancellation after confirmation is forbidden; use Refund" — a `CONFIRMED` payment never transitions to `VOIDED`, only to `REFUNDED` via a `CommercialRefund`).

## PaymentAllocation (new model, Milestone 11)

| From | To | Trigger |
|---|---|---|
| (none) | active | allocate |
| active | reversed | reversal (requires authority + reason) |

No further transitions — a reversed allocation is terminal; re-allocating the freed Payment amount creates a new allocation row.

## CommercialRefund (`REFUND_STATUSES`)

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `APPROVED` | approve |
| `DRAFT` | `VOID` | void before approval |
| `APPROVED` | `PAID` | confirm |
| `APPROVED` | `VOID` | void before payout |

No transition out of `PAID`/`VOID`.

## CommissionLedgerEntry (`commission-domain-design.md`'s existing enum, reused unchanged)

| From | To | Trigger |
|---|---|---|
| (none) | `EARNED` (this phase always goes straight here; `PENDING` reserved for a future clawback-window rule type, per existing 9.5A design — not built this phase) | payment confirmed + eligibility evaluation |
| `EARNED` | `APPROVED` | approve |
| `APPROVED` | `PAID` | payout batch confirmed |
| `EARNED`/`APPROVED`/`PAID` | (new `REVERSED`-type row created, original row's own status column is untouched) | refund of source payment |
| `EARNED`/`APPROVED` | `CANCELLED` | administrative cancellation (e.g. data-entry error, distinct from a refund-triggered reversal) |
| any | `DISPUTED` | dispute raised (schema-reserved; dispute *resolution* workflow not specified by the current spec text received — flagged as a possible gap pending the truncated remainder) |

## CommissionPayoutBatch (existing enum, reused unchanged)

| From | To | Trigger |
|---|---|---|
| `DRAFT` | `APPROVED` | batch approval |
| `APPROVED` | `PAID` | payout recorded |

No transition out of `PAID`.
