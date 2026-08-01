# Phase 9.5A Milestone 1 — Existing Owner Capability Audit

Real, read-only audit of `owner/app/` and `owner/migrations/versions/`, performed before any new
design. Full detail was gathered via direct source reads; summarized here by area.

## 1. Authentication — `owner/app/models/staff.py`

`StaffUser` (auth-only: email, password_hash, is_active, is_super_admin, mfa_required, session_version,
disabled_at/reason) — **no employee-profile fields** (no phone/job-title/department). `Role`,
`Permission`, `RolePermission`, `StaffRoleAssignment`, `StaffSession`, `StaffInvitation`,
`MfaCredential`, `MfaRecoveryCode`, `LoginAttempt`. Lockout computed at runtime from `LoginAttempt`
rows, not a stored field.

## 2. Customers — `owner/app/models/customers.py`

`Customer` (legal_name, trade_name, organization_type, country/city, tax_identifier,
lifecycle_status default `"LEAD"`, acquisition_source, assigned_sales_staff_id,
assigned_support_staff_id, archived_at) + `CustomerContact`, `CustomerAddress`, `CustomerNote` already
exist. Real ownership-assignment fields (`assigned_sales_staff_id`/`assigned_support_staff_id`) already
present — reusable directly for the new ownership/isolation policy (Milestone 7), not rebuilt.

## 3. Catalog — `owner/app/models/catalog.py`

`Product`, `Platform`, `ProductPlatform`, `ReleaseChannel`, `ProductVersion`, `Plan`, `PlanPrice`
(effective-dated, historical rows never overwritten), `Addon`, `EntitlementDefinition`,
`PlanEntitlement`/`AddonEntitlement`. Fully reusable as the commercial catalog authority (Milestone 10)
— no new product/plan/pricing table needed.

## 4. Subscriptions — `owner/app/models/subscriptions.py`

`Subscription` (device_allowance, platform_allowance, sales/support owner staff IDs, status), plus
`SubscriptionItem`, `SubscriptionAddon`, `SubscriptionStatusHistory`, `RenewalRecord`, and
**`PaymentRecord`** (customer_id, subscription_id, amount Numeric(12,2), currency, method, status,
recorded_by/verified_by staff) + `PaymentCorrectionHistory`. This is the one and only existing payment
system — reused, never duplicated.

## 5. Licenses — `owner/app/models/licensing.py`

`License` (device_limit, status, key_secret_hmac, allowed_platforms), `LicenseStatusHistory`,
`LicenseEntitlement`, `LicenseKeyIssuanceEvent`. Untouched this phase per the explicit "do not weaken
Phase 8 licensing" boundary.

## 6. Installations — `owner/app/models/installations.py`

`Installation` (status, fingerprint_hash, device_public_key, activation_count), plus
`InstallationStatusHistory`, `DeviceRecord`, `ActivationEvent`. Untouched.

## 7. Device-slot control — `owner/app/commercial_ops/device_slot_ops.py`

`DeviceSlotException` (extra_slots, starts_at/expires_at, status) + `resolve_effective_device_limit()`
+ `scan_over_limit_licenses()` — the real, existing, already-hardened (Phase 8V-P9) commercial-control
authority. Milestone 3's job is the missing *policy layer above* this (per-platform limits, plan
defaults, approval-required activation) — not a replacement.

## 8. Notifications — `owner/app/models/commercial_ops.py`

`InternalNotification` (notification_type, severity, dedup_key unique, status: OPEN/ACKNOWLEDGED/
IN_PROGRESS/RESOLVED/DISMISSED/EXPIRED) — a real, ready-made internal queue, directly reusable for
daily-report/dashboard alerting (Milestones 15-16) without a new table.

## 9. Audit — `owner/app/audit/services.py`

`AuditLog`, real hash-chained `record()` + `verify_chain()` — the one audit authority, extended with
new `action_code` values only (Milestone 23), never a parallel audit table.

## 10. Scheduler — `deploy/staging/run_scheduled_ops.py` + `owner/app/cli.py`

Real Postgres-advisory-lock scheduler (Phase 9) wraps `flask commercial expiry-scan/reconcile/
device-limit-scan` + backup + `verify_chain()`. The daily-snapshot job (Milestone 15) is designed to
plug into this same real mechanism, not a new scheduler.

## 11. RBAC seed — `owner/app/staff/seed_data.py`

69 permissions, 5 roles (SUPER_ADMIN/SALES/SUPPORT/FINANCE/VIEWER) confirmed real and already
reasonably scoped (SALES already has customer/subscription/license-create permissions; FINANCE already
has payment permissions). Extended, not replaced (Milestone 17).

## 12. Migrations — `owner/migrations/versions/`, current head `0f8d55b753ed`

Six revisions, in order: `62e4adb0a7b9` (initial schema) → `60f363ee66e8` (Phase 6 licensing/
activation) → `8646da2df010` (Phase 8 M1 renewal/payment-correction) → `0b1d294dfb40` (Phase 8 M3
commercial policy/notifications) → `65397e1b63e1` (Phase 8 M4 pilot/emergency-extension) →
`0f8d55b753ed` (Phase 8 M5 activation-approval/device-slot-ops, current head).

## 13. Genuinely absent (confirmed via full-codebase grep, not assumed)

Lead/Prospect, Quote, (Commercial) Invoice as a distinct document from `PaymentRecord`, Commission,
Expense, employee Presence/attendance, generic Location capture, and management notes beyond the
existing per-customer `CustomerNote` — **none exist in any form**. These are the real, genuinely new
surface this phase designs.

## 14. `releases` blueprint

Read-only browse UI over `ProductVersion`/`ReleaseChannel` — not a deployment pipeline. Unrelated to
this phase's scope beyond noting it exists (was misidentified as a possible distribution mechanism in
Phase 9's `private-artifact-distribution.md` — confirmed here it is exactly that: a real, existing
starting point, still accurate).

## Full detail

See `existing-domain-reuse-matrix.md` for the per-capability classification and
`duplication-risk-report.md` for the specific risks this audit closes off.
