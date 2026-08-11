# Phase 9.5D — Subscription/License Integration Report

## Fields populated on `create_subscription()`

`customer_id` (from the Order), `product_id`/`plan_id` (from the fulfilled `SalesOrderLine`'s plan, resolved to its parent `Product`), `sales_order_id` (the existing Phase 9.5A bookkeeping FK — this is what this milestone actually populates for the first time), `device_allowance` (from `Plan.included_device_count`), `sales_owner_staff_user_id` (the actor confirming fulfillment). `start_date`/`end_date`/`billing_cycle` are **not** set this milestone — a real, named gap: term-date derivation from `Plan.billing_model`/`billing_interval_months` is not implemented, so a fulfilled Subscription has no explicit term window yet. Not silently claimed complete.

## Fields populated on `create_license()`

`customer_id`, `subscription_id` (the just-created Subscription), `product_id`/`plan_id`, `allowed_platforms` (derived from the plan's real supported platforms — see the fulfillment contract's "real bug found" section), `device_limit` (from `Plan.included_device_count`). `allowed_release_channel_id`/`valid_from`/`valid_until` are **not** set this milestone — the same term-date gap as Subscription.

## Why `transition_subscription()`, not a direct status assignment

`app/models/subscriptions.py`'s `VALID_TRANSITIONS` table intentionally keeps `EXPIRED` terminal (a Phase 8 security fix preventing the generic transition route from reviving an expired subscription without the renewal-approval pipeline). Fulfillment only ever needs `DRAFT`→`ACTIVE`, a transition the table already allows unconditionally — calling the real function (which also writes `SubscriptionStatusHistory` and an audit record) rather than a bare assignment keeps this path consistent with every other subscription-status change in the codebase.

## Real gaps this milestone does not close (named, not hidden)

- Only `NEW_SUBSCRIPTION` fulfillment intent is implemented. `RENEWAL` (would need to route through `commercial_ops/renewal_requests.py`'s existing pipeline, per that module's own design note, rather than a bare `create_subscription()` call), `ADD_ON`, `DEVICE_POLICY_CHANGE`, `COMMERCIAL_EXTENSION` are not — an Order whose only line is an add-on cannot be fulfilled this milestone (`FULFILLMENT_NOT_ELIGIBLE`, explicit rejection, not a silent skip).
- Subscription/License term dates (`start_date`/`end_date`/`valid_from`/`valid_until`) are left `NULL` — a real, undone piece of work, not an oversight hidden behind a default value.
- Multi-line Orders (one Plan line + one or more Addon lines) only fulfill the Plan line; Addon lines are currently inert at fulfillment time (no `AddonEntitlement` application happens). Named limitation for a future pass.
