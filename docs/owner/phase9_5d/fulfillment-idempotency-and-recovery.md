# Phase 9.5D — Fulfillment Idempotency and Recovery

## Mechanism

Reuses the exact real pattern already proven in `app/leads/conversion.py::convert()` and `app/licensing/services.py::issue_license_key()`: the shared `CommercialOperationsIdempotencyKey` ledger, keyed on `(idempotency_key, operation_code="ORDER_FULFILL")`. A replay with the same key returns the original Subscription/License references (`{"replayed": True, ...}`) without re-executing any side effect.

## Two nested idempotency keys, deliberately

`fulfill_order()`'s own idempotency key gates the whole operation; a **derived** key (`f"{idempotency_key}-license"`) is passed to `issue_license_key()` specifically. This is deliberate, not accidental: `issue_license_key()` has its own independent idempotency ledger check (`LicenseKeyIssuanceEvent`), and deriving a distinct-but-linked key means a retry of the *whole* fulfillment operation also correctly replays the license-issuance step specifically, rather than colliding with some unrelated caller's use of the same raw key against `issue_license_key()`.

## Failure recovery

The write sequence (`create_subscription` → `transition_subscription` → `create_license` → `issue_license_key` → order status update → idempotency record) is wrapped in a `try`/`except`: any exception triggers `db_session.rollback()` and a `FULFILLMENT_FAILED` audit record before re-raising. `create_subscription`/`create_license` each commit their own transaction internally (their own established, unchanged behavior from Phase 6/8) — `fulfill_order()` cannot wrap the whole sequence in one atomic outer transaction without changing code outside `app/commercial_sales/`, which is out of this milestone's scope.

**Real gap found, then fixed, before this shipped**: because the idempotency-key record is written last, a crash between Subscription creation and that final commit would leave a real, committed `Subscription` row with no matching idempotency-key record — a naive retry would find no replay match and attempt to create a **second** `Subscription` for the same order. Fixed with a recovery guard: before calling `create_subscription()`/`create_license()`, `fulfill_order()` first checks whether a row already exists for this `sales_order_id`/`subscription_id` and reuses it instead of creating a duplicate. This makes the retry path safe independent of exactly where a prior attempt failed, not only when it failed after the final idempotency-key commit. `issue_license_key()`'s own idempotency ledger (`LicenseKeyIssuanceEvent`) already made that specific call safely retryable on its own; the guard above extends the same property to the Subscription/License creation steps that precede it.

Proven by `test_recovery_guard_reuses_existing_subscription_after_simulated_partial_failure` — simulates exactly this crash point (a `Subscription` created and linked to the order, but no idempotency-key record yet) and confirms a fresh `fulfill_order()` call reuses the existing Subscription rather than creating a second one.
