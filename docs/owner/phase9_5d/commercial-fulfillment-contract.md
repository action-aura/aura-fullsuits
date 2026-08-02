# Phase 9.5D — Milestone 13: Commercial Fulfillment Contract

`app/commercial_sales/fulfillment.py::fulfill_order()` — the single most severe-risk milestone in this phase (Non-Negotiable Principle 5). Never inserts `Subscription`/`License`/`Installation` rows directly — every mutation goes through the existing canonical services.

## Eligibility chain (all real, all enforced before any write)

1. `order.status == "CONFIRMED"` (`FULFILLMENT_NOT_ELIGIBLE`).
2. A non-`VOID` `CommercialInvoice` exists for the order.
3. `invoice.status == "PAID"` — the default `FULL_CONFIRMED_PAYMENT_REQUIRED` policy. No partial-payment fulfillment path exists this milestone (an alternative policy would require explicit configuration + management permission + reason + audit, none of which is implemented — no silent override).
4. No `CommercialRefund` in `DRAFT`/`APPROVED`/`PAID` status exists against the invoice (a blocking refund).
5. At least one `SalesOrderLine` with `plan_id` set exists — an add-on-only order has nothing this milestone knows how to fulfill (a real, named gap: `ADD_ON`/`RENEWAL`/`DEVICE_POLICY_CHANGE`/`COMMERCIAL_EXTENSION` fulfillment intents are not implemented; only `NEW_SUBSCRIPTION`).
6. Idempotency key not already used for a different order (`IDEMPOTENCY_CONFLICT`); already-`FULFILLED` order rejected (`FULFILLMENT_ALREADY_COMPLETE`).

## The write sequence — canonical services only

1. `app/subscriptions/services.py::create_subscription()` — real function, not a direct `Subscription(...)` construction inline. Starts `DRAFT`.
2. `app/subscriptions/services.py::transition_subscription(subscription, "ACTIVE", ...)` — the existing, real transition function (not a direct `.status = "ACTIVE"` assignment).
3. `app/licensing/services.py::create_license()` — `DRAFT`.
4. `app/licensing/services.py::issue_license_key()` — the one real key-issuance authority; the plaintext key is returned once, never persisted, never logged.
5. `order.status = "FULFILLED"`, `order.fulfilled_at` set.

Proven structurally, not just by convention, by `test_fulfillment_module_never_constructs_subscription_or_license_directly` — greps the actual module source for a direct model-row insert and finds none, matching the exact verification method already used for Phase 9.5C's `leads/conversion.py`.

## Real bug found and fixed before this shipped

The first draft set `License.allowed_platforms = "ALL"` as a placeholder. `app/licensing_service/activation.py`'s real activation check does `request_platform in license_row.allowed_platforms.split(",")` — an **exact membership test** against real platform codes (`"WINDOWS"`, `"ANDROID"`). The literal string `"ALL"` would never match any real platform code, meaning every activation attempt against a fulfilled license would have been silently rejected. Fixed by deriving the actual `allowed_platforms` from `catalog_for_sales.describe_plan_for_sale()`'s `supported_platform_codes` (Milestone 4, already computes this) — proven by `test_fulfill_order_creates_active_subscription_and_issued_license` asserting `"ALL"` is never present in the issued license's platform list.

## No Installation created

`test_no_installation_created_by_fulfillment` — fulfilling an order creates zero `Installation` rows. Device activation remains a separate, later event (unchanged Phase 6/8 behavior), never triggered by this phase's fulfillment path.

## M13 closure — 8 explicit items required before treating this milestone as permanently closed

Requested as a final verification pass after the milestone's initial implementation. All 8 are real, distinct tests, not variations of the same scenario:

1. **Subscription created, License creation crashes, retry reuses Subscription** — `test_item1_subscription_created_license_crashes_retry_reuses_subscription`. Simulates the exact partial-failure state directly, confirms the retry reuses the existing Subscription and completes the License step that "crashed."
2. **Subscription and License exist, result write crashes, retry reconciles without duplication** — `test_item2_subscription_and_license_exist_result_write_crashes_retry_reconciles`. Simulates both rows already existing with the order still `CONFIRMED`; retry reconciles to `FULFILLED` without creating a second Subscription or License.
3. **Two concurrent fulfillment requests for the same source line** — `test_item3_concurrent_fulfillment_requests_only_one_creates_subscription`. Five real threads racing `fulfill_order()` against the same order; asserts exactly one Subscription ever exists. Required a real fix: `db_session.refresh(order, with_for_update=True)` takes a row lock on the Order at the start of the write path — without it, two concurrent calls could both read `status == "CONFIRMED"` before either committed.
4. **Existing incompatible Subscription or License state** — `test_item4_existing_incompatible_subscription_state_rejected`. A Subscription already `CANCELLED` for this order (something happened to it outside this fulfillment attempt) is rejected, not silently reused or revived. Required a real fix: a `_RESUMABLE_SUBSCRIPTION_STATUSES = ("DRAFT", "ACTIVE")` allowlist gates the recovery-reuse path; anything else raises `FULFILLMENT_NOT_ELIGIBLE` instead of proceeding.
5. **Duplicate idempotency key, identical payload** — `test_item5_duplicate_idempotency_key_identical_payload_replays`.
6. **Duplicate idempotency key, conflicting payload** — `test_item6_duplicate_idempotency_key_conflicting_payload_rejected`. The same key reused against a genuinely different order is rejected (`IDEMPOTENCY_CONFLICT`), never silently resolved to either order.
7. **Refund after fulfillment invokes the configured entitlement consequence** — `test_item7_refund_after_fulfillment_invokes_entitlement_consequence`. Closes Milestone 12's own forward reference now that this milestone's fulfillment link exists — see `refund-entitlement-consequence-policy.md`.
8. **No direct write to Subscription/License/Entitlement/Installation/licensing-audit tables from commercial-sales services** — `test_item8_no_direct_write_to_licensing_or_installation_tables`. Structural scan across every file in `app/commercial_sales/`, not just `fulfillment.py`.

## Test coverage

`tests/test_phase9_5d_fulfillment.py` — 15 tests total: the 8 closure items above, plus the original happy path (Subscription `ACTIVE`, License `ISSUED` with real platform codes, Order `FULFILLED`), rejection when order isn't confirmed, rejection when invoice isn't fully paid, rejection when a blocking refund exists, already-fulfilled rejection, no-Installation proof, and the direct-construction structural proof (fulfillment.py only, superseded in scope by item 8's package-wide version).
