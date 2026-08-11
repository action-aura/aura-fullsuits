# Phase 9 — Health/Readiness Contract

## `/health/live`

Always `200 {"status": "ok"}` if the process can execute Python at all. No dependency calls. Used by
the container runtime / process manager to decide "should this worker be restarted", never "should
traffic be routed here".

## `/health/ready`

Real dependency checks, `200` when all blocking checks pass, `503` otherwise:

1. `database_connectivity` — `SELECT 1` against the real configured database.
2. `migration_at_head` — compares the real `alembic_version` table row against the real Alembic
   `ScriptDirectory` head revision (short-circuited, not run, if the DB check already failed).
3. Every check `flask commercial preflight` runs (Phase 8V-P9): active signing key, signing-key
   sign/verify round-trip, trust-anchor/active-key match, permission seed completeness, no duplicate
   permission codes, role/permission sync, license-pepper presence + placeholder detection + self-test
   round-trip + stray-alias detection, Super Admin MFA enforcement.

Only `name` and `status` (`OK`/`WARNING`/`FAIL`) are ever returned — never the `detail` free-text field
preflight itself carries, even though that field is already designed not to contain a literal secret.
`WARNING` does not fail readiness (matches `flask commercial preflight`'s own blocking/non-blocking
distinction — e.g. the dev pepper placeholder is a WARNING, not a FAIL). Confirmed no secret/connection
string ever appears in the response body (`test_ready_never_leaks_a_secret_value`).

## Real evidence (this session, local dev Owner + real local Postgres 17)

```
$ curl http://127.0.0.1:5561/health/live
{"status":"ok"}

$ curl http://127.0.0.1:5561/health/ready
{"checks":[{"name":"database_connectivity","status":"OK"},
           {"name":"migration_at_head","status":"OK"},
           {"name":"active_signing_key_exists","status":"OK"},
           {"name":"signing_key_sign_verify_roundtrip","status":"OK"},
           {"name":"trust_anchor_matches_active_key","status":"OK"},
           {"name":"all_permissions_seeded","status":"OK"},
           {"name":"no_duplicate_permission_codes","status":"OK"},
           {"name":"role_permissions_synced","status":"OK"},
           {"name":"license_pepper_configured","status":"WARNING"},
           {"name":"license_pepper_self_test_roundtrip","status":"OK"},
           {"name":"license_pepper_no_stray_aliases","status":"OK"},
           {"name":"super_admin_mfa_required","status":"WARNING"}],
 "ready":true}
```

Both `WARNING`s are expected and correct in this dev environment (dev pepper placeholder; no Super
Admin account without MFA has been created yet in this fresh instance) — neither is a `FAIL`, so
`ready: true` is the correct verdict.

A real DB-outage simulation (`test_ready_fails_closed_when_database_unreachable`) confirms the endpoint
fails closed (`503`, `ready: false`) and correctly skips the migration check rather than crashing on it
when the DB is genuinely down.
