# Subscription / Entitlement / License Map (M7.4)

Real connection between License and Customer/Subscription/Product/
Plan/Platform/entitlement/dates/renewal/grace/channel/device cap, and
determination of which real authority owns device limits.

## Real object graph

```
Customer (1) ──< Subscription (N) ──> Product (1)
                        │                  │
                        │                  ├──< ProductPlatform >── Platform
                        │                  └──< Plan (N, scoped to Product only)
                        │                              │
                        └──< License (N) ───────────────┘
                                  │  (customer_id, subscription_id, product_id, plan_id all
                                  │   independently FK'd on License — License does not derive
                                  │   them transitively through Subscription)
                                  └──< Installation (N) ──> DevicePublicKey (1 active + history)
```

Real citation for "License FKs all four independently rather than
deriving through Subscription": `app/models/licensing.py:17-24`
(`customer_id`, `subscription_id`, `product_id`, `plan_id` are four
separate NOT NULL FK columns on `License`, not a single
`subscription_id` with the rest resolved via join). This means the
real schema *permits* — though no code path currently constructs — a
License whose `product_id`/`plan_id` disagree with its own
`subscription_id`'s `product_id`/`plan_id`. No constraint or trigger
enforcing agreement was found in the migration
(`62e4adb0a7b9_initial_owner_schema.py:452-483`). Recorded as a real,
unenforced consistency assumption — not a bug fixed in M7 (out of
scope; would be an `OWNER_SERVER` gap per
`licensing-gap-ownership-matrix.md`).

## Dates / renewal / grace / channel

- `Subscription.start_date` / `end_date` / `renewal_date` /
  `cancellation_date` (`subscriptions.py:23,24,29,30`) — real, direct
  columns.
- `License.valid_from` / `valid_until` (`licensing.py:30-31`) — a
  **separate** validity window from the Subscription's own dates,
  checked independently at activation
  (`licensing_service/activation.py:154-159`).
- **Grace period**: not a per-Subscription or per-License column.
  Policy-driven via `CommercialPolicy.payment_grace_days` /
  `past_due_start_days` / `auto_expire_after_grace`
  (`app/models/commercial_ops.py:243-244,255`), consumed by the
  scheduled `expiry_scan.py` job.
- **Renewal**: `RenewalRecord` (`subscriptions.py:106-124`) plus the
  dedicated `commercial_ops/renewal_requests.py` approval workflow —
  the one place a terminal Subscription status
  (`EXPIRED`/`PAST_DUE`/`SUSPENDED`) can be revived to `ACTIVE`
  (`_REVIVABLE_SUBSCRIPTION_STATUSES`, `renewal_requests.py:62`).
- **Release channel**: `License.allowed_release_channel_id`
  (`licensing.py:26-28`, nullable FK to `owner_release_channels`) —
  checked at activation (`activation.py:123-127`,
  `RELEASE_CHANNEL_NOT_ALLOWED`). `Plan.release_channel_id`
  (`catalog.py:95-97`) is a separate, catalog-level default; no code
  path was found that copies `Plan.release_channel_id` onto
  `License.allowed_release_channel_id` at issuance time (issuance sets
  it from the staff-submitted form value,
  `app/licensing/routes.py:46-64`, not from the Plan).

## Which real authority owns device limits

Per `owner-licensing-authority-audit.md`'s "Plan" section, four
independent representations exist. Resolving "which one is
authoritative":

| Layer | Field | Real role |
|---|---|---|
| Catalog default | `Plan.included_device_count` / `Plan.max_device_count` | Advisory default shown when drafting a subscription/license; **not read** by activation. |
| Commercial | `Subscription.device_allowance` | Set once at subscription-create time (`subscriptions/routes.py:61`); **not read** by activation. |
| Entitlement | `PlanEntitlement(entitlement_code="max_devices")` | Generic JSONB entitlement row; consulted by `entitlement-preview` staff tooling (`licensing_admin/routes.py:146-153`), **not read** by activation. |
| **Enforced** | `License.device_limit` | **The only field `process_activation()` actually enforces**, via `resolve_effective_device_limit()` (`commercial_ops/device_slot_ops.py:122-135`, = `License.device_limit` + any active in-window `DeviceSlotException.extra_slots`). |

**Real, authoritative answer: `License.device_limit` is the single
enforced authority.** The other three are real, persisted, but
currently advisory/informational only — none of them feed
`License.device_limit` automatically at any point in the real code
(issuance, renewal, or replacement). A mobile client must therefore
only ever trust `allowed_device_count` as returned in the signed
assertion payload (`assertions.py:49`, sourced from
`license_row.device_limit` directly), never a Plan- or
Subscription-level number, since the latter two are not guaranteed to
agree with what activation actually enforces.

## Entitlements

`LicenseEntitlement` (`licensing.py:79-89`, per-license JSONB
override) vs. `PlanEntitlement` (`catalog.py:140-151`, per-plan
default) — both real, distinct tables; entitlement resolution
(`entitlement-preview` route, `licensing_admin/routes.py:146-153`)
layers License-level entitlements over Plan-level defaults. Real
entitlement codes seeded: `max_devices`, `allowed_platforms`,
`release_channel`, `backup_enabled` (`app/catalog/services.py:36-47`,
several marked "(not yet built)" in source comments — i.e. real rows
exist for capabilities not yet implemented anywhere else in Owner).

## Real test coverage

`owner/tests/test_phase6_entitlements.py`,
`owner/tests/test_phase9_5d_entitlement_consequence.py`,
`owner/tests/test_commercial_ops_activation_policy.py::test_approve_
pending_activation_rechecks_device_limit` (re-verifies the effective
limit, not any Plan/Subscription number, is what's checked).
