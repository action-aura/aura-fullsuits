# Owner Licensing Authority Audit (M7.2)

Real, per-entity audit of every domain object the mobile licensing
contract will eventually touch, built entirely from tier-1/tier-2
evidence (`licensing-authority-source-map.md`). All paths relative to
`owner/` unless stated otherwise. This document only records what
Owner already does; it implements nothing.

## Customer

- **Model/table**: `app/models/customers.py:14-49`, `owner_customers`.
- **Service**: `app/customers/services.py`.
- **Route**: `app/customers/routes.py`, prefix `/customers`, staff-only
  (`load_current_staff()`).
- **Fields**: `legal_name`, `trade_name`, `organization_type`,
  `country`, `city`, `tax_identifier`,
  `commercial_registration_reference`, `primary_language`,
  `timezone`, `lifecycle_status` (default `LEAD`),
  `acquisition_source`, `assigned_sales_staff_id`,
  `assigned_support_staff_id`, `archived_at`,
  `converted_from_lead_id`, `version` (optimistic lock).
- **Constraints/indexes**: FK to `owner_staff_users` (sales/support
  assignee), FK to `owner_leads` (conversion trace).
- **Lifecycle states**: `lifecycle_status` free string, observed
  values `LEAD`/`ARCHIVED` in tests; archiving never hard-deletes
  (`tests/test_customers.py:30-46`).
- **Idempotency**: not applicable — CRM record creation is a manual
  staff action, not a retried protocol call.
- **Transaction boundary**: single-request, ORM session scope.
- **Authn/authz requirement**: staff session + (implicit, not
  independently audited here) customer-management permission.
- **Audit behavior**: `CustomerNote` provides a manual audit trail;
  no automatic `CustomerStatusHistory` table was found (unlike
  Subscription/License/Installation, which each have a dedicated
  `*StatusHistory` table).
- **Current tests**: `tests/test_customers.py` (create, duplicate
  warning, archive-no-hard-delete, contact/note).
- **Mobile compatibility**: Customer itself is never sent to or
  received by a mobile client under the current contract — only
  opaque `license_public_id`/`installation_public_id` values
  traceable back to a Customer server-side. See
  `owner-data-minimization-contract.md`.
- **Discovered gap**: no customer-facing identity exists at all (see
  `customer-authentication-gap-analysis.md`) — Customer is pure
  CRM data, never a login principal.

## Customer Contact / customer account or portal identity

- **Model**: `CustomerContact` (`app/models/customers.py:51-68`,
  `owner_customer_contacts`) — `name`, `title`, `business_email`,
  `business_phone`. No password/session/token field of any kind.
- **Portal identity**: **confirmed absent.** No customer-facing
  login, password, session, or portal exists anywhere in `owner/app/`
  — see `customer-authentication-gap-analysis.md` for the full
  negative-evidence trail (grep results across `app/customers/`,
  `app/auth/`, `app/api/`, `app/api_external/`, `app/models/
  customers.py`).

## Subscription

- **Model/table**: `app/models/subscriptions.py:14-63`,
  `owner_subscriptions`.
- **Service**: `app/subscriptions/services.py`.
- **Route**: `app/subscriptions/routes.py` (staff-only; `device_
  allowance` set at create time, `subscriptions/routes.py:61`).
- **Fields**: `customer_id`, `product_id`, `plan_id`, `status`
  (default `DRAFT`), `start_date`, `end_date`, `billing_cycle`,
  `auto_renew_preference`, `device_allowance`, `platform_allowance`
  (free string, not FK), `renewal_date`, `cancellation_date`,
  `cancellation_reason`, `sales_owner_staff_user_id`,
  `support_owner_staff_user_id`, `internal_notes`,
  `created_by_staff_user_id`, `approved_by_staff_user_id`,
  `sales_order_id` (Phase 9.5A addition).
- **Constraints/indexes**: FKs to `owner_customers`, `owner_products`,
  `owner_plans`, `owner_staff_users` (x4), `owner_sales_orders`.
- **Lifecycle states**: real `VALID_TRANSITIONS`,
  `app/subscriptions/services.py:11-16` — `DRAFT → {PILOT, ACTIVE,
  CANCELLED}`, `PILOT → {ACTIVE, COMPLETED, CANCELLED}`, `ACTIVE →
  {PAST_DUE, SUSPENDED, EXPIRED, CANCELLED}`, `PAST_DUE → {ACTIVE,
  SUSPENDED, EXPIRED, CANCELLED}`, `SUSPENDED → {ACTIVE, CANCELLED,
  EXPIRED}`. `EXPIRED` is terminal for the generic path (regression-
  tested, `tests/test_subscriptions.py:67-88`); the one real exception
  is the renewal-approval flow, which can revive
  `EXPIRED`/`PAST_DUE`/`SUSPENDED` back to `ACTIVE`
  (`app/commercial_ops/renewal_requests.py:62,358`).
- **Idempotency**: not a retried device-facing protocol; not
  applicable at this layer.
- **Transaction boundary**: single ORM session per staff action;
  `expiry_scan.py` runs as a scheduled/manual batch job, one
  transition per eligible subscription.
- **Authn/authz**: staff session; permission-gated (not independently
  itemized here — no `subscriptions.*` permission string was searched
  for in this pass).
- **Audit behavior**: `SubscriptionStatusHistory`
  (`subscriptions.py:90-103`, `from_status`/`to_status`/
  `changed_by_staff_user_id`/`reason`).
- **Current tests**: `tests/test_subscriptions.py` (transitions,
  history, cancellation-from-terminal rejected, EXPIRED-stays-terminal
  regression, payment correction history).
- **Mobile compatibility**: Subscription is never sent directly to a
  mobile client; its `status` feeds `subscription_status` in the
  signed assertion payload (`assertions.py`, see
  `installation-credential-contract.md`).
- **Discovered gap**: no dedicated per-subscription grace-period
  column — grace is policy-driven via `CommercialPolicy.payment_
  grace_days`/`past_due_start_days` (`app/models/commercial_ops.py:
  243-244,255`), consumed by `expiry_scan.py`. This is a real,
  intentional design (policy-level, not per-row) — recorded as a fact,
  not a defect.

## Product

- **Model/table**: `app/models/catalog.py:14-24`, `owner_products`.
- **Real rows today**: exactly two — `AURA_RETAIL` ("Aura Retail"),
  `AURA_CLINIC` ("Aura Clinic") (`app/catalog/services.py:28-31`). A
  Product is a whole application family, not a feature/SKU.
- **Fields**: `product_code` (unique), `name`, `description`,
  `is_active`, `commercial_status` (default `PILOT`), `pilot_status`,
  `is_sellable` (comment: `AURA_OWNER itself = False`),
  `deprecated_at`.
- **Mobile compatibility**: `product_code` is a real, required field
  in the activation request schema, enum-constrained to
  `["AURA_RETAIL","AURA_CLINIC"]`
  (`contracts/activation-request-v1.schema.json`).

## Platform

- **Model/table**: `app/models/catalog.py:27-31`, `owner_platforms` —
  a real DB-backed table, not a bare string column.
- **Real seeded rows today**: exactly two — `WINDOWS`, `ANDROID`
  (`app/catalog/services.py:32`). No `IOS` row exists.
- **iOS trace**: two unrelated string-constant occurrences elsewhere
  in the codebase (`DEVICE_POLICY_PLATFORM_CATEGORIES` in
  `app/models/activation_governance.py:17-20`, an internal device-cap
  policy category set never joined to `owner_platforms`; and
  `PRESENCE_PLATFORMS` in `app/models/employees.py:26`, staff-presence
  heartbeat tracking, unrelated to customer licensing) — neither is a
  real Platform row, neither is accepted by any licensing contract.
  Full detail and citations in `platform-authority-audit.md`.

## ProductPlatform

- **Model/table**: `app/models/catalog.py:34-45`,
  `owner_product_platforms` — real junction, `UniqueConstraint
  (product_id, platform_id)`, `supported: bool`.
- **Real use**: catalog-level "what does this Product ship for at
  all" mapping, seeded 1:1 for every Product × Platform pair
  (`app/catalog/services.py:95-102`). It does **not** gate an
  individual license — see Platform Authority below and
  `platform-authority-audit.md` for the two-mechanism finding.

## Plan

- **Model/table**: `app/models/catalog.py:81-102`, `owner_plans`.
- **Fields**: `plan_code` (unique), `product_id`, `name`,
  `description`, `lifecycle_status` (default `DRAFT`),
  `billing_model` (`ONE_TIME`/`MONTHLY`/`ANNUAL`/`PILOT`/`CUSTOM`),
  `billing_interval_months`, `currency`, `included_device_count`
  (default 1), `max_device_count` (nullable), `support_level`,
  `release_channel_id`, `effective_date`, `retirement_date`.
- **Relationships**: `PlanPrice` (append-only price history, never
  overwritten, `tests/test_catalog.py:26-48`), `PlanEntitlement` →
  `EntitlementDefinition` (JSONB `value`, real codes include
  `max_devices`, `allowed_platforms`, `release_channel`,
  `backup_enabled`, `app/catalog/services.py:36-47`).
- **Scoping**: Plan belongs to exactly one Product (`product_id`);
  Plan has no `platform_id` — platform scoping happens elsewhere (see
  Platform Authority).
- **Discovered gap (device-limit reconciliation)**: device/seat caps
  exist in **four independent real representations** with **no
  reconciliation code between them**:
  1. `Plan.included_device_count` / `Plan.max_device_count`
     (`catalog.py:92-93`) — catalog-level plan defaults.
  2. `Subscription.device_allowance` (`subscriptions.py:27`) — set
     independently at subscription-create time
     (`subscriptions/routes.py:61`), not derived from `Plan.max_
     device_count` at write time.
  3. `License.device_limit` (`licensing.py:29`, NOT NULL, default 1)
     — the field actually enforced at activation
     (`licensing_service/activation.py:240-242`).
  4. `PlanEntitlement(entitlement_code="max_devices")` — a fourth,
     generic JSONB representation seeded via `app/catalog/services.py:
     36-47,104-112`.
  No code path was found that reconciles (1)/(2)/(4) against the
  authoritative (3) at license-issuance time; each is set
  independently by whichever staff workflow creates that row. This is
  a real drift risk flagged for `licensing-gap-ownership-matrix.md`
  (classification: `OWNER_SERVER` — a mobile client cannot fix this;
  it only ever reads the effective `License.device_limit` +
  `DeviceSlotException` total via `resolve_effective_device_limit()`,
  `commercial_ops/device_slot_ops.py:122-135`).

## License

- **Model/table**: `app/models/licensing.py:14-104`, `owner_licenses`.
- **Full field list, lifecycle states, and transitions**: see
  `license-state-machine-contract.md` (M7.3) for the complete,
  dedicated audit — summarized here: real states `DRAFT/ISSUED/
  ACTIVE/SUSPENDED/EXPIRED/REVOKED/REPLACED` (not `ARCHIVED`),
  `VALID_TRANSITIONS` at `app/licensing/services.py:15-23`.
- **Key handling**: full plaintext key generated once at issuance
  (`issue_license_key()`, `app/licensing/services.py:57-65`), never
  persisted — only `key_prefix`/`key_suffix_masked`/`key_secret_hmac`
  (unique) are stored (`licensing.py:37-43`). Proven end-to-end by
  `owner/tests/test_phase6_activation_protocol.py::test_full_key_
  never_appears_anywhere_in_db_logs_or_error_response`.
- **Child tables**: `LicenseStatusHistory`, `LicenseEntitlement`,
  `LicenseKeyIssuanceEvent` (idempotency-keyed key issuance).

## Installation

- **Full audit**: `installation-authority-contract.md` (M7.6).
  Summary: real per-device row (`app/models/installations.py:17-56`,
  `owner_installations`), real per-installation Ed25519 credential
  (`DevicePublicKey`, `app/models/licensing_service.py:35-51`, unique
  `fingerprint`) — confirmed **not** a shared per-license token.
  `VALID_TRANSITIONS`: `app/installations/services.py:18-25`.
  `SLOT_CONSUMING_STATUSES = (REGISTERED, PENDING_ACTIVATION, ACTIVE,
  SUSPENDED)` (line 16) — these count against `device_limit`.

## Installation credential (device key)

- See `installation-credential-contract.md` (M7.12) for the full
  audit of `DevicePublicKey` lifecycle (register/rotate/revoke) and
  the separate `SigningKey` (Owner's own assertion-signing key).

## Activation request / activation result

- **Full audit**: `activation-idempotency-contract.md` (M7.10) and
  `remote-licensing-api-contract-map.md` (M7.9).
  Real function: `process_activation()`,
  `app/licensing_service/activation.py:77-349`. Row-locks the License
  (`with_for_update()`, line 175-177), checks license/subscription
  status and validity window, checks the effective device limit,
  creates or reuses an `Installation`, optionally routes to
  `PENDING_ACTIVATION` (manual-approval policy), signs an assertion,
  commits once. Concurrency correctness is test-proven
  (`owner/tests/test_phase6_concurrency.py`), not merely claimed.

## Refresh (check-in) / deactivation / replacement / expiry / suspension / revocation / reactivation

- **Check-in**: `process_checkin()`, `app/licensing_service/
  checkin.py:47-151` — device-signature-authenticated, no license key
  required, rejects on installation `SUSPENDED/DEACTIVATED/REPLACED`
  or license `SUSPENDED/REVOKED/EXPIRED`, re-signs a fresh assertion
  on success.
- **Deactivation**: `process_deactivation()`,
  `app/licensing_service/deactivation.py:37-115` — idempotent, revokes
  the device key, sets `Installation.status = DEACTIVATED`.
- **License-level suspend/revoke/reactivate**: `transition_license()`,
  `app/licensing/services.py:104-128`, via
  `POST /licenses/<id>/transition` — per-target RBAC permission
  (`licenses.revoke`/`licenses.suspend`/`licenses.reactivate`) plus
  `require_recent_auth` (≤10-minute MFA). **Does not cascade** to
  child Installations automatically — installations are only blocked
  indirectly, at their next check-in/activation attempt.
- **Replacement**: `replace_license()`, `app/licensing/services.py:
  131-160` — issues a new `DRAFT` license, old license transitions to
  `REPLACED` (if it was `EXPIRED`) or `REVOKED` (otherwise). Separate,
  narrower device-level replacement exists via
  `replace_device_slot()`/`replace_device_key()`
  (`commercial_ops/device_slot_ops.py`, `licensing_service/
  device_identity.py:124-139`).
- **Expiry**: no automatic license-row expiry job was found in
  `app/licensing/` or `app/licensing_service/` — `License.status`
  reaching `EXPIRED` requires a manual staff transition call. (Note:
  the *subscription*-level expiry scan, `commercial_ops/
  expiry_scan.py`, is date-driven and automatic — but it transitions
  `Subscription.status`, not `License.status`, directly.)

## Release channel / application version / release manifest

- Full audit: `mobile-release-version-contract.md` (M7.14). Real
  `ReleaseChannel`/`ProductVersion` models (`app/models/catalog.py:
  48-79`) with real `artifact_checksum_sha256`, `is_current_stable`,
  `is_deprecated`. License-level channel/platform allowlisting is a
  real, enforced activation gate. No minimum-app-version enforcement
  contract exists anywhere (confirmed absent by grep, see M7.14).

## Audit event

- `ActivationEvent` (`app/models/installations.py:89-107`,
  `owner_activation_events`) — real audit trail of every activation/
  deactivation/check-in decision (`event_type`, `result`,
  `reason_code`, `correlation_id`). Separate from
  `LicenseStatusHistory`/`SubscriptionStatusHistory`/
  `InstallationStatusHistory`, which record staff-driven transitions.

## Cross-cutting real findings carried into later M7 documents

1. Four-location device-limit representation with no reconciliation
   (Plan) — feeds `multi-device-license-policy-audit.md` and
   `licensing-gap-ownership-matrix.md`.
2. No customer-facing authentication anywhere — feeds
   `customer-authentication-gap-analysis.md`.
3. Two non-unified platform-restriction mechanisms
   (`ProductPlatform` catalog-level vs. `License.allowed_platforms`
   denormalized string) — feeds `platform-authority-audit.md`.
4. No minimum-app-version enforcement — feeds
   `mobile-release-version-contract.md`.
5. License-level status transitions never cascade to Installations —
   feeds `license-state-machine-contract.md` and
   `installation-authority-contract.md`.
