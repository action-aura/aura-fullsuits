# Phase 5 -- Aura Owner Threat Model

Scope: the Owner Foundation application as built this phase (internal, not internet-deployed, no external API active).

| # | Threat | Mitigation this phase | Residual risk |
|---|---|---|---|
| 1 | Stolen/weak staff password | Argon2id hashing, password policy, login throttling, MFA mandatory for Super Admin | Non-Super-Admin roles can be configured MFA-optional; acceptable at internal-staff scale, tracked in residual-risk register |
| 2 | Session hijacking / fixation | Server-side sessions, HttpOnly+Secure+SameSite cookies, session regenerated on login, idle + absolute timeout, revocation list | No IP-pinning (would break legitimately mobile staff); accepted |
| 3 | Privilege escalation via hidden UI | Every route re-checks permission server-side (`@require_permission`); hidden buttons are never the only control | Requires ongoing discipline on every new route -- covered by RBAC direct-API-call tests |
| 4 | License-key leakage via logs/audit | Only masked prefix/suffix ever logged or audited; full key exists only in the HTTP response body of the single issuance request | A staff member could screenshot the one-time reveal screen -- out of scope for a software control |
| 5 | Brute-forcing license-key secrets offline (DB leak) | HMAC-SHA256 with server-side pepper (env-only, never in DB) over >=128 bits of entropy | If both DB and pepper leak together, keys are crackable -- standard trade-off, documented |
| 6 | Enumeration of customers/licenses/staff via sequential IDs | Public UUIDs everywhere; permission check before any lookup; generic 404 for both "not found" and "not permitted" | None beyond timing side-channels, not addressed this phase |
| 7 | CSRF on state-changing routes | Flask session-bound CSRF token, required on every POST/PUT/PATCH/DELETE | N/A |
| 8 | SQL injection | SQLAlchemy ORM/parameterized queries exclusively; no raw string-interpolated SQL anywhere in the codebase | Verified by code search in `owner-security-report.md`, not by a fuzzing tool |
| 9 | Audit tampering (insider or DB-level edit) | Hash-chain (`previous_hash`/`current_hash`); `verify_chain()` detects any row edited outside the audit service | Chain detects tampering, does not prevent a superuser with raw DB access from editing and then "fixing" the chain by recomputing forward -- documented as an accepted limitation of an application-level (not database-level, e.g. WORM storage) control |
| 10 | Forbidden business/medical data entering Owner by accident (future integration) | Allowlist serializers + forbidden-field test guard (Part W) | Only covers what's built this phase; must be re-applied to every future contract |
| 11 | Owner database backup exposure | Backup restricted to Super Admin + recent MFA, stored outside web root, never served over HTTP | Physical/OS-level access to the backup directory is outside this application's control |
| 12 | Unauthorized external activation traffic | `OWNER_EXTERNAL_API_ENABLED=false` by default; no route registered when disabled (not just an early-return -- the blueprint itself is not attached) | N/A this phase (no such traffic can reach a live route) |
| 13 | Invitation token replay/guessing | Token generated via `secrets.token_urlsafe(32)` (>=256 bits), stored only as a hash, one-time use enforced at the DB row level, short expiry | N/A |
| 14 | Denial of service via login endpoint | Per-identifier + per-IP login throttling | No distributed rate-limit store (single-process); acceptable for an internal, low-traffic app; would need Redis at real scale |

**Superseded (Phase 9R M7, 2026-08-04):** the "single-process" residual risk
in #14 no longer describes the current implementation.
`app/security/ratelimit.py`'s `is_locked_out()`/`record_attempt()` are
backed by the persistent `owner_login_attempts` PostgreSQL table, queried
identically by every Gunicorn worker process — already a real distributed
store, not in-memory, verified under simulated multi-worker concurrency in
`owner/tests/test_phase9r_rate_limit_multi_worker.py`. This entry is kept
as a historical record of Phase 5's own accurate assessment at the time,
not edited in place.

## Explicitly out of scope this phase
Network-level threats (this app is not deployed to a reachable network yet), physical security of the host, supply-chain compromise of a pinned dependency, multi-instance/distributed rate limiting.
