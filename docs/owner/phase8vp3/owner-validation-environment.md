# Phase 8V-P3 — Owner Validation Environment

## Real preflight result against the live `aura_owner_dev` database

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
    {"name": "super_admin_mfa_required", "status": "WARNING", "detail": "2 Super Admin account(s) have mfa_required=False: ['phase8vp-admin@example.com', '941d8bec-approver@example.com']. Fine for local synthetic test accounts; must not be true for any real production Super Admin."}
  ]
}
```

`ok: true`, exit code 0. The one WARNING is expected -- both flagged accounts are this project's own
synthetic test staff created in prior sessions (Phase 8V-P), not real production accounts. Environment
is genuinely ready on the Owner side.

## Owner API URL path

Real Owner routes are served under `/api/licensing/v1` (`owner/app/licensing/routes.py` blueprint
prefix, unchanged since Phase 6). Confirmed unchanged this session -- not re-derived, since no Owner
route code changed.

## Android-side URL configuration -- real finding, not previously documented

The Android build does **not** hardcode an Owner URL. `android/aura-clinic/app/build.gradle` (and
the Retail equivalent) define `OWNER_LICENSING_BASE_URL` as a `buildConfigField` sourced from a
Gradle property (`-PownerLicensingBaseUrl=...`), **empty by design** when that property isn't passed
-- the same "never a hidden fallback URL" fail-safe principle the Windows products use
(`products/clinic/backend/config.py`). Checked the actual compiled value in the currently-built rc.3
release artifacts:

```
$ grep OWNER_LICENSING_BASE_URL android/aura-clinic/app/build/generated/source/buildConfig/release/.../BuildConfig.java
public static final String OWNER_LICENSING_BASE_URL = "";
$ grep OWNER_LICENSING_BASE_URL android/aura-retail/app/build/generated/source/buildConfig/release/.../BuildConfig.java
public static final String OWNER_LICENSING_BASE_URL = "";
```

Both are empty. **This is expected and correct for a generic/unconfigured build** -- it is not a bug
-- but it means the rc.3 artifacts currently in `dist/android/` will report licensing
`NOT_CONFIGURED` if installed as-is, and are **not yet usable for a licensing physical-validation
session**. See `artifact-verification.md` for the exact rebuild command needed once a device is
connected, using `adb reverse` so the on-device URL can safely be `http://127.0.0.1:<port>/api/licensing/v1`
without any LAN or public exposure -- consistent with this phase's own "controlled localhost, adb
reverse, or isolated LAN connectivity" requirement, and consistent with the full path suffix always
being required end-to-end (no repeat of the historical missing-`/api/licensing/v1`-suffix defect,
since the field's own value must be the complete path, not a bare host, by the client library's own
contract in `commercial_runtime/licensing_contracts/client.py`).

## Real PostgreSQL, migrations, signing key

`alembic current` / `alembic heads` both report `0f8d55b753ed (head)` (see
`docs/owner/phase8vp2/final-regression-report.md`, unchanged this session since no migration was
added). Active signing key `owner-ed25519-20260727T053324Z-c32537d7`, real sign/verify round-trip
succeeded (see preflight output above). No public internet exposure introduced or required.
