# Phase 9.5A — Device Policy API Contract (design; not routed this phase)

## `GET /api/operations/v1/device-policies/{plan_id}/effective`

Permission: `device_policy.view`. Returns the resolved policy for a plan's default profile (no
subscription context).

```json
{
  "plan_id": "uuid",
  "max_total_devices": 2,
  "platform_rules": [{"platform_category": "WINDOWS", "max_devices": 1},
                      {"platform_category": "MOBILE", "max_devices": 1}],
  "approval_required_after_device_number": null,
  "source": "PLAN_PROFILE | GLOBAL_DEFAULT | UNRESTRICTED"
}
```

## `GET /api/operations/v1/subscriptions/{subscription_id}/device-policy/effective`

Permission: `device_policy.view`. Same shape, `as_of` query param optional (default now), resolves
plan profile + any active override.

## `POST /api/operations/v1/subscriptions/{subscription_id}/device-policy/overrides`

Permission: `device_policy.manage`. Body: `max_total_devices` (nullable), `platform_rules` (nullable
list), `approval_required_after_device_number` (nullable), `reason` (required), `effective_from`,
`effective_until` (nullable). Creates a new override row (never edits an existing one — closes the
prior active row's `effective_until` instead). Emits `DEVICE_POLICY_OVERRIDE_CREATED` audit event
(Milestone 23). Idempotency key required (Milestone 19 standard).

## Error codes used

`PERMISSION_DENIED`, `RECORD_NOT_FOUND` (unknown plan/subscription), `VALIDATION_ERROR` (e.g.
`effective_until <= effective_from`), `DEVICE_POLICY_VIOLATION` (reserved for the future live-enforcement
phase's rejection responses — defined now so the error catalog is stable before enforcement exists).
