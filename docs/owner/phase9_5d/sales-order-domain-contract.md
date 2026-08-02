# Phase 9.5D — Milestone 8: Sales Order Domain Contract

`app/commercial_sales/sales_orders.py`, built directly on the existing `SalesOrder`/`SalesOrderLine` schema (Phase 9.5A) — no new model, no migration this milestone.

## Functions

- `create_order_from_quote(quote, *, actor_employee_profile_id, actor_staff_user_id, idempotency_key) -> SalesOrder` — the only creation path (Order-without-a-Quote is explicitly not built this phase; the spec's "approved direct-order exception" is not implemented). Requires `Quote.status == "ACCEPTED"`. Resolves the Customer via Milestone 7's `resolve_customer_for_accepted_quote()` (never reimplemented — this is where a Lead-based Quote's conversion actually happens if it hasn't already). Snapshots every `QuoteLine` into a new `SalesOrderLine` (copying the already-immutable price snapshot, not re-resolving against the catalog). Idempotent via the shared `CommercialOperationsIdempotencyKey` ledger (`OPERATION_CODE = "SALES_ORDER_CREATE_FROM_QUOTE"`), matching `leads/conversion.py`'s exact pattern.
- `confirm_order(order, ...)` — `DRAFT`→`CONFIRMED`.
- `cancel_order(order, *, reason, ...)` — `DRAFT`/`CONFIRMED`→`CANCELLED` (not `FULFILLED` — no fulfillment path exists yet this milestone; Milestone 13 will add the `CONFIRMED`→`FULFILLED` transition and the corresponding cancellation guard).

## One active Order per accepted Quote

Checked explicitly, before the idempotency-key lookup even applies: a genuinely *different* idempotency key submitted against a Quote that already has a non-cancelled Order raises `IDEMPOTENCY_CONFLICT` — the same code used for a genuine idempotency-key reuse-across-different-targets conflict (matching `leads/conversion.py`'s own reuse of that code for an analogous "this key must always resolve to the same real thing" rule). A cancelled Order does not block a fresh one (not implemented as a re-order flow this milestone, but the check doesn't structurally prevent it either).

## Ownership

`apply_ownership_filter()` extended with a `SalesOrder` branch — creator-only (`created_by_employee_profile_id`), same rule as `Quote` (no separate assignee concept on either model).

## Negative-space proof

`test_order_creates_no_invoice_payment_subscription_license_commission` — creating and confirming an Order creates zero `CommercialInvoice`/`PaymentRecord`/`Subscription`/`License`/`CommissionLedgerEntry` rows (Non-Negotiable Rule 2: Order is not an Invoice; confirming does not mean paid or fulfilled).

## Test coverage

`tests/test_phase9_5d_sales_orders.py` — 9 tests: creation from an accepted Quote (lines/total snapshot correctly), rejection of a not-yet-accepted Quote, idempotent replay, one-active-Order-per-Quote enforcement, confirm/cancel transitions, reason-required-on-cancel, confirmed-cannot-re-confirm, stale-version rejection, and the negative-space fulfillment-document proof.
