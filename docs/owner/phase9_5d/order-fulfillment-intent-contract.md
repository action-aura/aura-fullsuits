# Phase 9.5D — Order Fulfillment Intent Contract

## Scope of this document at Milestone 8

`SalesOrder` has no dedicated "fulfillment intent" field in the existing Phase 9.5A schema — the model records only lifecycle status (`DRAFT`/`CONFIRMED`/`CANCELLED`/`FULFILLED`) and the standard document fields. The governing spec's Milestone 13 introduces the actual fulfillment intents (`NEW_SUBSCRIPTION`/`RENEWAL`/`ADD_ON`/`DEVICE_POLICY_CHANGE`/`COMMERCIAL_EXTENSION`); this document records what Milestone 8 establishes as the foundation those intents will act on.

## What "confirmed" means, precisely

`confirm_order()` transitions `DRAFT`→`CONFIRMED` and sets `confirmed_at`. This records that the Order itself (the line items, quantities, and total) is locked in and ready to be invoiced — it makes no claim about payment or fulfillment (Non-Negotiable Rule 2). `SalesOrderLine` rows are never edited after an Order leaves `DRAFT` (no `update_order_line()` function exists, matching Quote's own immutability-once-submitted pattern).

## What `FULFILLED` will mean (forward reference to Milestone 13)

`CONFIRMED`→`FULFILLED` is not a transition this milestone implements (`SALES_ORDER_TRANSITIONS["CONFIRMED"]` in `app/commercial_sales/errors.py` already includes `FULFILLED` as a valid target, reserved for Milestone 13's fulfillment orchestration service to use once the eligibility chain — Invoice issued, Payment confirmed and allocated, no blocking refund — is real). `cancel_order()`'s transition table deliberately does not allow cancelling a `FULFILLED` Order (`SALES_ORDER_TRANSITIONS["CONFIRMED"]` permits `CANCELLED`, but once `FULFILLED` there is no further transition at all in the table) — a fulfilled sale can only be undone through a `CommercialRefund` (Milestone 12), never a bare Order-level cancellation.

## Traceability into fulfillment (existing, unchanged)

`Subscription.sales_order_id` (Phase 9.5A, additive nullable FK) already exists as the bookkeeping link Milestone 13 will populate when a Subscription is created for a confirmed, paid Order — confirmed present in the schema by Milestone 1's audit, not something this milestone adds.
