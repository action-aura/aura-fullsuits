# Phase 9.5D — Refund Entitlement Consequence Policy

`app/commercial_sales/entitlement_consequence.py::determine_entitlement_consequence(*, order_was_fulfilled, is_full_refund) -> str` — Non-Negotiable Rule 7's explicit policy function, never a silent choice.

## Decision matrix (all 4 branches tested)

| Order fulfilled? | Refund type | Outcome |
|---|---|---|
| No | Full | `NO_CONSEQUENCE` |
| No | Partial | `NO_CONSEQUENCE` |
| Yes | Full | `SUSPEND_ENTITLEMENTS` (the configured default) |
| Yes | Partial | `NO_CONSEQUENCE` (documented policy: the customer received something real, a partial refund doesn't by itself imply the product should stop working) |

## Why "before fulfillment: nothing to do" is correct, not a gap

Non-Negotiable Rule 7's first bullet ("before fulfillment: cancelled/refunded sale must not create Subscription or License") is satisfied structurally — Milestone 13 (not yet built) is the only thing that would ever create a Subscription/License from a sale, and its own eligibility check (a blocking refund is one of its stated preconditions) prevents fulfillment from proceeding at all once a refund exists. There is nothing this milestone needs to *do* to enforce that half of the rule — it's a property of ordering, verified once Milestone 13 exists to check.

## Now wired to a real mutation (closed after Milestone 13 landed)

`SUSPEND_ENTITLEMENTS`/`REVOKE_ENTITLEMENTS`/`RETAIN_WITH_APPROVED_EXCEPTION` are outcomes that act on a real `Subscription`/`License` row — which only exists once Milestone 13's fulfillment orchestration has created one and linked it back to the `SalesOrder` (via the existing `Subscription.sales_order_id` FK, Phase 9.5A). Originally left as a marked forward-reference comment in `confirm_refund()`, now closed: `refunds.py::_apply_entitlement_consequence()` looks up the invoice's `SalesOrder` (`invoice.sales_order_id`), determines `order_was_fulfilled` from its status, calls `determine_entitlement_consequence()`, and — when the result is `SUSPEND_ENTITLEMENTS` — finds the linked `Subscription` (`Subscription.sales_order_id == order.id`) and transitions it `ACTIVE`→`SUSPENDED` via the canonical `transition_subscription()` (never a direct status assignment), recording an `ENTITLEMENT_CONSEQUENCE_APPLIED` audit event. Proven end-to-end by `test_item7_refund_after_fulfillment_invokes_entitlement_consequence`: fulfill an order, confirm a full refund against its invoice, assert the real Subscription is `SUSPENDED`.

`REVOKE_ENTITLEMENTS` and `RETAIN_WITH_APPROVED_EXCEPTION` remain real, valid outcomes the pure decision function can produce, but the current default policy never selects them automatically — still a real, named limitation (an irreversible outcome or an explicit management override is out of this milestone's scope).

## Default policy is real but deliberately conservative

`DEFAULT_FULL_REFUND_CONSEQUENCE = SUSPEND_ENTITLEMENTS` — reversible, matching this codebase's existing preference for reversible states (`EmergencyExtension`'s own short-lived, revocable design) over irreversible ones. `REVOKE_ENTITLEMENTS` is a real, valid return value this function *can* produce but the current default policy never selects automatically — an irreversible outcome requires an explicit management override, which is out of this pure-decision function's scope and not implemented this phase (a real, named limitation, not silently assumed away).

## Test coverage

`tests/test_phase9_5d_entitlement_consequence.py` — 4 tests, one per matrix cell above. Pure function, no DB/app fixtures required.
