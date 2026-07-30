# Phase 8V-P2 — Environment Preflight Report

## What was built

`owner/app/commercial_ops/preflight.py` + `flask commercial preflight` CLI command
(`owner/app/cli.py`). Read-only, no secrets printed, exits nonzero on any blocking mismatch. Checks:

| Check | Blocking? | What it catches |
|---|---|---|
| `active_signing_key_exists` | Yes | No ACTIVE row in `owner_signing_keys` |
| `signing_key_sign_verify_roundtrip` | Yes | Reuses the existing `verify_signing_key_health()` real sign/verify round-trip |
| `trust_anchor_matches_active_key` | Yes if file present and stale; WARNING if absent | The exact Phase 8V-P gap: `trust_anchor.json` referencing a key that isn't the active one |
| `all_permissions_seeded` | Yes | The exact Phase 8V-P gap: `Permission` rows missing vs. `app/staff/seed_data.py` |
| `no_duplicate_permission_codes` | Yes | Duplicate codes in the `PERMISSIONS` source list itself |
| `role_permissions_synced:<ROLE>` | Yes | A role missing a `RolePermission` row for a code it should have |
| `super_admin_mfa_required` | No (informational) | A Super Admin account with `mfa_required=False` |

Trust anchor path defaults to the repo-relative conventional location
(`commercial_runtime/licensing_contracts/trust_anchor.json`) and is overridable via
`COMMERCIAL_TRUST_ANCHOR_PATH` for tests/CI. Its absence is a WARNING, not a FAIL -- a fresh clone
with no product built yet legitimately has no such file.

## Automated tests: 8/8 passing

`owner/tests/test_commercial_ops_preflight.py` -- covers the no-active-key case, the healthy-by-
default case, missing-permission detection, missing-role-assignment detection, missing/stale/
matching trust anchor, and the non-blocking super-admin-MFA warning.

## Real result against the actual `aura_owner_dev` database (2026-07-30)

First run -- found a **third, previously undetected, genuine environment drift**:

```
"role_permissions_synced:SUPER_ADMIN": FAIL -- missing 10 permission(s): [
  'activation_policy.manage', 'device_slot_exceptions.manage', 'device_slot_exceptions.view',
  'emergency_extensions.create', 'emergency_extensions.revoke', 'emergency_extensions.view',
  'pending_activations.decide', 'pending_activations.view', 'pilots.manage', 'pilots.view'
]
```

The same ten permission codes the Phase 8V-P session added as `Permission` catalog rows were never
actually linked to the `SUPER_ADMIN` role via `RolePermission` -- despite that session's own report
claiming the role-permission mappings were re-synced. In practice this had **no live effect**: real
`is_super_admin=True` staff bypass the role-permission join entirely
(`app/security/rbac.py::get_staff_permission_codes()` grants every `Permission` row directly to a
super admin, never consulting `RolePermission` for that case), and this codebase's own
`create-superadmin` CLI always sets `is_super_admin=True` alongside the `SUPER_ADMIN` role, so nobody
holds that role without also holding the bypass. It is still a real, genuine data-consistency defect
this preflight command was built specifically to catch, and it was silently masking the fact that a
hypothetical non-`is_super_admin` staff member assigned the `SUPER_ADMIN` role would have been
missing ten permissions.

Fixed with the existing, idempotent `flask seed-rbac` command (no new capability, no schema change):

```
$ flask seed-rbac
Seeded 69 permissions and 5 roles.
```

Re-run:

```
$ flask commercial preflight
{
  "ok": true,
  "checks": [
    {"name": "active_signing_key_exists", "status": "OK", "detail": "Active key: owner-ed25519-20260727T053324Z-c32537d7"},
    {"name": "signing_key_sign_verify_roundtrip", "status": "OK", "detail": "Real sign/verify round-trip succeeded."},
    {"name": "trust_anchor_matches_active_key", "status": "OK", "detail": "trust_anchor.json recognizes owner-ed25519-20260727T053324Z-c32537d7."},
    {"name": "all_permissions_seeded", "status": "OK", "detail": "All 69 permission codes present."},
    {"name": "no_duplicate_permission_codes", "status": "OK", "detail": "No duplicate permission codes."},
    {"name": "role_permissions_synced", "status": "OK", "detail": "All 5 roles have every permission code defined in code."},
    {"name": "super_admin_mfa_required", "status": "WARNING", "detail": "1 Super Admin account(s) have mfa_required=False: ['phase8vp-admin@example.com']. Fine for local synthetic test accounts; must not be true for any real production Super Admin."}
  ]
}
```

`ok: true`, exit code 0. The one WARNING is expected and correct -- `phase8vp-admin@example.com` is
the Phase 8V-P session's own deliberately-no-MFA synthetic test account, documented in that phase's
own handover.

## Net effect

The environment is now in a genuinely clean, preflight-passing state, and the next session (or any
future one) gets a single command that would have caught all three real environment gaps found
across Phase 8V-P and Phase 8V-P2 on day one, instead of finding them by hand mid-session.
