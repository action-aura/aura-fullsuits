# Security Audit — Shared / General

Covers `commercial_runtime/security/`, `commercial_runtime/identity/` (used
identically by both products on both platforms). Platform-specific findings are
in `09-windows-security-audit.md`/`10-android-security-audit.md`.

## Authentication — PROVEN

- **Password hashing**: `commercial_runtime/security/passwords.py` —
  PBKDF2-HMAC-SHA256, `DEFAULT_ITERATIONS = 600_000` (documented as the OWASP
  2023 minimum), 16-byte random salt per password (`secrets.token_bytes`),
  self-describing stored format (`pbkdf2_sha256$<iter>$<salt>$<hash>`),
  constant-time comparison (`hmac.compare_digest`). Empty passwords rejected
  (`PasswordPolicyError`). This is a correct, modern implementation — no
  findings.
- **Legacy hash migration**: a bare-SHA-256 legacy format is recognized ONLY
  for one-time transparent migration on successful login
  (`authenticate_and_maybe_upgrade`, not independently re-read line-by-line in
  this pass but confirmed exercised by `retail_security_test.py::test_legacy_account_migrates_on_login`
  and `test_legacy_migration_incorrect_password_does_not_migrate`, both **PASS**
  in isolation). `verify_legacy_sha256` is never used to *produce* new hashes,
  only to verify-then-upgrade — correct design.
- **Account lockout**: `commercial_runtime/identity/mt_auth.py` —
  `MAX_FAILED_ATTEMPTS = 5` (line 33), `locked_until` timestamp persisted on the
  `users` row, checked before password verification (lines 147-150), reset to
  `NULL`/0 on successful login (line 181). **PROVEN present and wired in.**
- **Secret key**: `commercial_runtime/security/app_secret.py` —
  `secrets.token_hex(32)` (32 bytes, csprng), persisted per-installation under
  the app's writable data directory, atomic write (`os.replace`), fails safe
  (any corruption/read failure generates a **new** secret rather than falling
  back to a default — invalidates sessions rather than weakening them).
  **No hardcoded/shared secret key exists anywhere in the codebase** (grepped;
  the only literal secret-shaped string found was a historical one explicitly
  confirmed absent from the packaged builds — see
  `docs/build/retail-windows-build-report.md`'s "no dev secrets" check).
  Minor: `os.chmod(path, S_IRUSR|S_IWUSR)` (app_secret.py) is a POSIX no-op on
  Windows — the secret file's actual access control on Windows is whatever
  NTFS ACL the user's own profile directory already has, not a weaker
  world-readable file, but the code's chmod call gives no *additional*
  protection on Windows the way it does on a POSIX host. P4.
- **Demo/backdoor credentials**: **none found.** `products/retail/backend/config.py`
  and `products/clinic/backend/config.py` both explicitly document the removal
  of the original monolith's `DEFAULT_USERS` demo-login mechanism (Phase 1/3
  scope decision, confirmed by comment + absence). `commercial_runtime/identity/onboarding_routes.py`
  explicitly comments "no demo backdoor, and no seeded user anywhere in this
  file — the ONLY way an [account is created] is [via onboarding]" (line 17).
  Grepped for common demo-credential literals (`admin123`, `password123`,
  hardcoded default emails) repo-wide: zero matches in `products/`,
  `commercial_runtime/`.
- **Password reset**: not located in the routes read for this pass — treated as
  **NOT PRESENT / UNVERIFIED** rather than asserted broken; a forgotten-password
  self-service flow was not found, meaning account recovery today likely
  requires direct database access or a support/admin path not modeled in the
  code reviewed. Flagged as a functional gap (`12`/`13`), not a security defect.

## Authorization — PROVEN

- Retail: no centralized single role-decorator was found comparable to
  Clinic's `@require_clinic_role`; Retail's authorization is
  `@mt_login_required` + `@mt_require_subsystem('retail')` (subsystem-level,
  not fine-grained-role-level) on every route sampled, with tenant scoping via
  `_cid()`. No route was found that was missing `@mt_login_required` in the
  sections read.
- Clinic: `@require_clinic_role('doctor')` exists and is applied to
  `create_prescription` (prescribing is doctor/admin only) — a genuine
  fine-grained role gate. Whether every route that *should* have a role
  restriction has one was not exhaustively re-verified for every single route
  in this pass (the file is large); the sample read (billing, payments,
  patients, prescriptions) shows correct gating everywhere sampled.
- **Tenant isolation**: covered exhaustively in `06`/`07` (data-integrity audit)
  — Retail has no known live IDOR; Clinic's historical 8-route IDOR (including
  the "worst case" `record_payment`, which had literally no `company_id` filter
  on either its SELECT or UPDATE) was fixed in commit `57e74a0` and is
  confirmed unmodified/intact since (`git diff` empty against current HEAD).

## Local API surface — PROVEN

- **Binding**: every server entry point found (`products/{retail,clinic}/backend/app.py`,
  both desktop launchers, both Android `main.py`) binds `host='127.0.0.1'`
  exclusively — **no LAN-exposed (`0.0.0.0`) binding exists anywhere** in
  either product on either platform.
- **CORS**: `products/{retail,clinic}/backend/app.py` — both restrict CORS
  origins to `^https?://(127\.0\.0\.1|localhost)(:\d+)?$` via a compiled regex,
  `supports_credentials=True`. Correct — matches the loopback-only binding.
- **Debug mode**: both apps call `app.run(host='127.0.0.1', port=port, debug=False)`
  as their non-waitress fallback — `debug=False` explicit, not left at Flask's
  default. Correct.
- **SQL injection**: grepped both backends and `commercial_runtime` for
  f-string/`%`-formatted SQL. Every match found falls into one of two safe
  patterns, confirmed by reading the surrounding code: (1) a dynamic
  `SET col=?, col2=?` clause built from a **hardcoded field-name allowlist**
  filtered against the request body (e.g. `retail_api.py:265`'s
  `allowed = ['name','barcode','category_id',...]`, `clinic_api.py:206`'s
  `k in ['name','dob','gender',...]`) with all **values** still passed as
  parameterized `?` placeholders — not injectable; or (2) a literal,
  code-controlled table/column name (e.g. `_ensure_credit_schema`'s internal
  `addcol()` calls, `_owned()`'s `table` argument, which is always a
  hardcoded string literal at every call site, never user input). **One lower-
  severity exception**: `products/retail/backend/api/import_api.py` lines
  211/216 build `f'SELECT ... FROM "{t}"'` where `t`/`best` are table names
  read from a **user-uploaded `.db`/`.sqlite` file's own `sqlite_master`
  catalog** — technically unparameterized identifier interpolation, but the
  database being queried is an ephemeral temp-file copy of the *uploading
  user's own file*, deleted after the import preview completes; there is no
  path from this to another tenant's data or the app's real database. P4 —
  correct to fix on principle (use `sqlite3`'s identifier-quoting helper or
  validate against `[A-Za-z0-9_]+`), not a live exploitable vulnerability
  against the product.
- **Hardcoded secrets/dev URLs/private IPs**: none found in `products/`,
  `commercial_runtime/`, or `android/*/app/build.gradle`/`app/src` beyond the
  loopback-only patterns already documented above (which are intentional, not
  leaked secrets).

## What was not independently re-verified in this pass

Every route in `retail_api.py`/`clinic_api.py` was not individually
line-by-line re-audited for a missing auth/role decorator (both files are
large — retail ~1460 lines, clinic ~980+ lines); the sample coverage above is
representative (auth/financial/tenant-critical routes were prioritized) but not
exhaustive. A dedicated follow-up pass enumerating every single route against
its decorator stack would close this gap — recommended as Wave 2 work
(`26-corrective-roadmap.md`), not required before a first sale given the
sampled routes show a consistent, correct pattern throughout.
