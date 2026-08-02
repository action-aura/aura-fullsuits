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

## Why the decision function is not yet wired to a real mutation

`SUSPEND_ENTITLEMENTS`/`REVOKE_ENTITLEMENTS`/`RETAIN_WITH_APPROVED_EXCEPTION` are outcomes that act on a real `Subscription`/`License` row — which only exists once Milestone 13's fulfillment orchestration has created one and linked it back to the `SalesOrder` (via the existing `Subscription.sales_order_id` FK, Phase 9.5A). `confirm_refund()` (this milestone) has a marked, explicit forward-reference comment at the exact point Milestone 13 will insert the real call — calling a function that has nothing real to act on yet would be worse than an honestly-absent integration point.

## Default policy is real but deliberately conservative

`DEFAULT_FULL_REFUND_CONSEQUENCE = SUSPEND_ENTITLEMENTS` — reversible, matching this codebase's existing preference for reversible states (`EmergencyExtension`'s own short-lived, revocable design) over irreversible ones. `REVOKE_ENTITLEMENTS` is a real, valid return value this function *can* produce but the current default policy never selects automatically — an irreversible outcome requires an explicit management override, which is out of this pure-decision function's scope and not implemented this phase (a real, named limitation, not silently assumed away).

## Test coverage

`tests/test_phase9_5d_entitlement_consequence.py` — 4 tests, one per matrix cell above. Pure function, no DB/app fixtures required.
