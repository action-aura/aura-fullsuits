# Phase 6 -- Offline-Grace Policy Authority

Phase 6 **defines and returns** offline policy. It does not enforce it inside Retail or Clinic -- no product code is touched this phase, per the governing spec's explicit boundary.

## `owner_offline_policies` fields (exactly Part P's list)
`policy_id` (code, e.g. `standard-v1`), `check_in_interval_seconds`, `retry_interval_seconds`, `offline_grace_seconds`, `warning_start_seconds`, `hard_expiry_behavior` (enum: `WARN_ONLY` / `RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA` -- deliberately no "lock/delete/corrupt data" option exists in the enum at all, structurally preventing that outcome per Principle 12 and Part P's explicit instruction), `clock_rollback_tolerance_seconds`, `assertion_refresh_threshold_seconds`, `emergency_extension_allowed` (bool), `emergency_extension_until` (nullable timestamp, staff-set), `policy_version`.

## Safe defaults (never unlimited)
Seeded default policy `standard-v1`: 24h check-in interval, 1h retry interval, 14-day offline grace, warning starts at 10 days, `hard_expiry_behavior=WARN_ONLY`, 5-minute clock-rollback tolerance, 1-day assertion-refresh threshold, no emergency extension by default. No policy field ever defaults to "unlimited" -- `offline_grace_seconds` has no sentinel meaning infinite; a genuinely long grace period must be an explicit, visible large number, not a magic -1.

## Assignment
`owner_license_offline_policy_assignments` links a License to a policy (defaulting to `standard-v1` if unassigned). Assignment is a staff action (`offline_policies.manage` permission) recorded in status history and audited.

## Distinctions this document defines (Part P's explicit documentation requirement)
- **License validity**: the License row's own `status`/`valid_from`/`valid_until` -- Owner's ground truth.
- **Assertion validity**: `not_before`/`expires_at` on one *issued* signed assertion -- shorter-lived, refreshed on check-in, bounded by `OWNER_ASSERTION_TTL_SECONDS`.
- **Check-in interval**: how often a healthy, online product is expected to check in (`check_in_interval_seconds`).
- **Offline grace**: how long a product may continue operating *without* a successful check-in before the policy's hard-expiry behavior applies (`offline_grace_seconds`).
- **Warning period**: the tail of the grace window where a product *should* (in a future phase's UI) start warning the user (`warning_start_seconds` before grace expiry).
- **Commercial restriction**: what `hard_expiry_behavior` permits Owner to *communicate* to a product once grace is exhausted -- restricting commercial/paid features is representable; nothing else is.
- **Data access preservation**: not a policy field but a structural guarantee -- no field in this table, no code path in this phase, and no future product integration built on this schema can express "delete/withhold/corrupt customer data" as a valid instruction, because the enum and the entire Owner-to-product contract (Part H's assertion allowlist) has no such vocabulary.

## What Phase 6 explicitly does not do
Enforce any of this inside Retail/Clinic. Implement subscription-expiry enforcement. That is Phase 7/8's decision, not begun here.
