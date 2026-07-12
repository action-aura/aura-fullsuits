# Aura Retail — Security Remediation Phase 1

Status: **complete**. Scope: authentication, session, destructive-operation, and
production-boundary hardening for the Retail standalone product. Financial
calculation correctness, the return workflow, printing, and backup/export are
explicitly **out of scope** — see "Remaining risks" in the completion report.

This document is the reference for what changed and why. No secrets or
credentials are included anywhere below.

---

## 1. Final authentication architecture

There is exactly **one** implementation of `POST /api/auth/login`, in
`api/auth.py`. It is the only route registered against that URL (verified by
`tests/retail_security_test.py::test_no_duplicate_login_route_registration`,
which walks `app.url_map`). It tries, in order:

1. **Registry-based login** (multi-tenant / standalone-product accounts —
   this is what Retail, Clinic, and any SaaS-registered company use) via
   `api/mt_auth.authenticate_registry_user(email, password)`. This single
   function is the only place a password is ever checked against the
   `registry.db` `users` table; every other route that used to duplicate this
   logic (`api/standalone_auth.py`'s old `/api/auth/login` route) now either
   doesn't exist or delegates to it.
2. **Legacy per-domain demo login** (`DOMAINS` in `config.py` — the
   Banking/Healthcare/Education/Manufacturing demo verticals, not Retail) —
   only reached if step 1 found no matching registry account and a `domain`
   was supplied.

`api/mt_auth.create_session(user)` is the single place that populates
`flask.session` after any registry-based login (normal login, the
onboarding wizard's auto-login of a freshly created admin, and employee
setup-link completion all call it), so every session has the same minimum
fields: `mt_user_id, company_id, employee_id, mt_role, clinic_role,
mt_session_version, require_password_change, email`.

**The hardcoded `baha.aura@admin` / `bahaa123` superadmin backdoor is
removed**, along with the `role_level >= 5` bypass in
`mt_login_required`/`mt_require_subsystem`/`require_clinic_role` that only
that backdoor could ever satisfy (no legitimate account can reach
`role_level >= 5` — the legacy `ROLES` dict in `config.py` caps `admin` at
level 4). No replacement hardcoded account was introduced.

A second, previously unaudited zero-credential bypass was found and closed
in the same pass: `POST /api/demo/start` (`api/demo_api.py`) set
`session['is_demo_mode']=True, session['company_id']=1, session['mt_role']=
'admin'` with **no authentication at all**, and `mt_login_required` /
`mt_require_subsystem` both unconditionally trust `is_demo_mode`. Since
Retail's schema defaults `company_id` to `1` for a standalone single-tenant
install (`products.company_id INTEGER DEFAULT 1`, etc.), this endpoint was a
complete, credential-free admin takeover of a real standalone customer's
actual data. It — and the rest of `api/demo_api.py`'s public demo portal —
is now only registered at all when `core.security.modes.retail_demo_mode_enabled()`
is true (see §5); it does not exist in the URL map of a shipped build.

## 2. Password hashing: algorithm and parameters

**Algorithm: PBKDF2-HMAC-SHA256**, 600,000 iterations (OWASP 2023 minimum),
16-byte random salt per password, constant-time verification
(`hmac.compare_digest`). Implementation: `core/security/passwords.py`.

Stored format (self-describing, so parameters/algorithm can change later
without a bulk-invalidation event):

```
pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
```

### Why not Argon2id or bcrypt (the plan's preferred first two choices)

Both require a compiled/native extension built per Android ABI
(`arm64-v8a`, `armeabi-v7a`, `x86_64`). This project's Android build
(Chaquopy) is deliberately restricted to pure-Python packages with universal
wheels — `android/DEPENDENCIES.md`'s own "Deliberately EXCLUDED" table
already rejects `pandas` for exactly this reason ("Heavy native dep"), and
neither `argon2-cffi` nor `bcrypt` appears anywhere in the project's pinned
Chaquopy pip list. There is no Android build environment or emulator
available in this environment to empirically confirm a native wheel would
resolve for all three ABIs — and the task instructions explicitly forbid
claiming Android compatibility on the strength of a Windows-only test run.

Given that documented precedent and the inability to verify Android
packaging here, PBKDF2-HMAC-SHA256 is the strongest option **known** to work
identically on both platforms today: it is pure Python standard library
(`hashlib`, `hmac`, `secrets`) — zero new pip dependency on either platform,
so there is nothing to fail to resolve on any Android ABI.

**Upgrade path**: when a real device/emulator build is available to confirm
wheel availability, add Argon2id as a second recognised prefix in
`core/security/passwords.py` (`argon2id$...`), make `hash_password()` prefer
it, and `needs_rehash()` will transparently upgrade every account the next
time it logs in — the same migration-on-login mechanism already used for the
SHA-256 → PBKDF2 transition (§3) handles this without code changes anywhere
else.

## 3. Legacy migration behaviour

Every account created before this phase has a `password_hash` that is a bare
64-character lowercase hex string (`hashlib.sha256(pwd.encode()).hexdigest()`)
with no salt. These accounts are **not** invalidated.

`core/security/passwords.authenticate_and_maybe_upgrade(password, stored_hash)`
is the single function every login path uses:

1. Try the modern PBKDF2 verifier first.
2. If that fails **and** the stored value matches the legacy 64-hex-char
   shape exactly, verify against `hashlib.sha256(password).hexdigest()`.
3. On a successful legacy match, the caller persists a freshly computed
   modern hash to the same row **in the same request** and records a
   `PASSWORD_HASH_UPGRADED` audit event (no password or hash value in the
   audit payload). The next login for that account uses the modern verifier
   from step 1.
4. A wrong password never migrates anything, at either the modern or legacy
   step. A malformed/garbage stored value is never treated as a legacy hash
   (only an exact 64-hex-char string qualifies) — verification just fails,
   safely, rather than crashing.

Applied everywhere a stored password is checked, not just Retail's own
login: `api/auth.py` (registry login — now delegated — and the legacy
`DOMAINS` login), `api/auth.py::customer_login` (the demo-vertical customer
portal). Places that only **create** a password (registration, admin
onboarding, employee setup-link completion, the downloadable-bundle
provisioning code in `api/system_download.py`) now call
`core.security.passwords.hash_password()` directly — there was nothing to
migrate there, only the hashing call itself needed swapping.

One place was deliberately **not** touched: the one-time invite/setup-link
token hash (`hashlib.sha256(token.encode())` in `api/auth.py` and
`api/standalone_auth.py`). A token is a high-entropy random value (128 bits
from `uuid4().hex`), not a low-entropy human password — hashing it with a
plain fast hash for lookup purposes is standard practice and does not need a
slow KDF. This was a deliberate scope decision, not an oversight.

## 4. Session-secret storage

`config.SECRET_KEY` is no longer a literal in source. `core/security/app_secret.py`
generates a 256-bit random value once per installation
(`secrets.token_hex(32)`), persists it under
`<app-data>/security/secret.key`, and reuses it on every subsequent start —
`<app-data>` is the same writable directory (`AURA_APP_DATA`) that already
holds `registry.db` and `config.json`, so it follows an existing,
already-writable-and-backed-up-by-the-user location rather than introducing
a new one.

- Two different installations (two different `AURA_APP_DATA` directories,
  i.e. two different customers, or Windows vs. the same customer's Android
  install) get two different, independently generated secrets.
- If the file is missing, unreadable, or fails validation (wrong length /
  not hex), a **new** secret is generated and persisted — the failure is
  logged (path and error type only, never any part of the secret), and any
  sessions signed with the old value are naturally invalidated (forces
  re-login). It never falls back to the old hardcoded literal or any other
  fixed value — this is checked directly by
  `tests/retail_security_test.py::test_no_hardcoded_fallback_literal_in_config`.
- `security/secret.key` is added to `.gitignore` so a developer's local
  secret is never accidentally committed.

**Recovery procedure** if a customer's `secret.key` is ever deleted or
corrupted: nothing to do — the app regenerates one automatically on next
launch. The only user-visible effect is that anyone currently logged in is
signed out and must log in again; no data is lost.

## 5. Production / development / demo mode matrix

`core/security/modes.py` is the single source of truth.

| Mode | How it's entered | What it unlocks |
|---|---|---|
| **Production standalone** | `sys.frozen` is True (a real PyInstaller `.exe`), or the Android launcher sets `AURA_STANDALONE=1` | Nothing extra — this is the default, most-restricted state. `dev_mode_enabled()` and `retail_demo_mode_enabled()` are hard-coded to `False` here regardless of any environment variable, because `IS_FROZEN` is checked first and short-circuits both. |
| **Development** | Running from source, `AURA_DEV=1` exported first | Skips the licence/activation check in `license_validator.py` (pre-existing behaviour, now formally gated through this module instead of being checked ad hoc). |
| **Demo portal / retail demo reset** | Running from source, `AURA_RETAIL_DEMO_MODE=1` exported first | (a) registers `api/demo_api.py`'s public `/api/demo/*` blueprint at all, (b) allows `POST /api/sub/retail/demo-seed` and `DELETE /api/sub/retail/demo-wipe` to proceed past their production-boundary guard (they still separately require company-admin session + an explicit confirmation token — see §6). |
| **Automated test** | No dedicated flag. Tests set `AURA_APP_DATA` to a `tempfile.mkdtemp()` directory and run from source (never frozen), so they naturally sit in the Development/Demo-portal gates above when a specific test needs to exercise them. | Isolated temp registry.db / retail.db / config.json / secret.key per test run; never touches real data. |

The reason `IS_FROZEN` is the hard boundary, not just "no env var set by
default": a customer running the real shipped `.exe` cannot toggle it by
setting an environment variable, because the check happens before the
environment variable is even read. This directly satisfies the requirement
that "an ordinary environment variable set by a customer" must never unlock
privileged behaviour in a production package.

## 6. Demo-mode restrictions (retail demo-seed / demo-wipe)

`POST /api/sub/retail/demo-seed` and `DELETE /api/sub/retail/demo-wipe`
(`api/subsystems/retail_api.py`) now require **all four**, in order:

1. `core.security.modes.retail_demo_mode_enabled()` — 404 if not (route
   behaves as if it doesn't exist; never reachable in a frozen build).
2. The caller's session is a company admin (`mt_role == 'admin'`) — 403
   otherwise. This is the finest-grained "administrator" distinction the
   current session model actually carries (see §7 on why full per-permission
   RBAC wiring for these two routes is deferred, not silently skipped).
3. An explicit, company-specific confirmation string in the request body —
   `{"confirm": "WIPE-<company_id>"}` / `{"confirm": "SEED-<company_id>"}` —
   400 otherwise. This can't be triggered by a stray click, CSRF, or a
   replayed request captured against a different company.
4. Every statement is scoped `WHERE company_id=?` (three child tables —
   `sale_items`, `return_items`, `purchase_order_items` — have no
   `company_id` column of their own and are scoped via a subquery against
   their parent's `company_id` instead), and the whole operation runs inside
   one `BEGIN TRANSACTION` with `rollback()` on any failure, so a
   half-completed wipe/seed can never leave the database inconsistent.
   `tests/retail_security_test.py::test_demo_wipe_rolls_back_on_failure`
   forces a mid-loop failure and asserts the already-executed delete was
   undone; `test_demo_wipe_scoped_to_own_company_only` proves a second
   company's rows are untouched.

Before this phase, both routes ran an **unscoped** `DELETE FROM` across
every retail table with no `company_id` filter at all, reachable by any
authenticated retail user (not just an admin), with no mode gate — any
logged-in user in any tenant could wipe every tenant's retail data with one
call. `database.subsystem_db._seed_retail()` was also found, while fixing
this, to hardcode `company_id=1` in every `INSERT` regardless of which
company the caller belonged to — meaning a multi-tenant demo-seed for any
company other than `1` would have silently written its demo rows into
company 1's tables instead. Both are fixed (`_seed_retail` now takes an
explicit `company_id` parameter, threaded through every insert).

## 7. Production/development mode matrix — RBAC scope note

`mt_require_subsystem` still enforces only a **binary**
`access_level != 'none'` per subsystem for non-admin employees (unchanged
from before this phase) — the granular named permissions defined in
`core/rbac/permissions.py` (`process_return`, `manage_cashier`, etc.) are
not wired into any retail route's enforcement path. Wiring that up is a
larger RBAC-integration change than "remove backdoors, fix hashing, fix
demo-endpoint scoping," and is explicitly out of this phase's task list.
What Phase 1 *does* guarantee is that authorization is always derived from a
**stored** account and its **stored** role/access-level row — never from a
hardcoded credential, a session flag anyone can set without logging in, or a
missing-data fail-open.

## 8. Known offline-license limitation (not a security control)

`api/mt_auth._is_module_enabled()` now fails **closed** for a tenant that
has been explicitly provisioned (any row at all in `company_modules` for
that `company_id`, e.g. via `POST /api/auth/register-company`) — a module
with no explicit row is treated as not licensed, closing a real bug where a
provisioned tenant could previously reach any module nobody had enabled for
them.

A standalone Retail install's onboarding flow
(`api/standalone_auth.py::create_admin`) never populates `company_modules`
at all — for that install type, the actual (soft) license source is the
local `config.json` bundle (`license_validator.py`, and the same file's
`modules` list). `_is_module_enabled()` falls back to reading that file only
from this specific installation's own `AURA_APP_DATA` directory (never from
the source tree / `BASE_DIR` — that fallback was tried, immediately caught a
real bug where a stray developer `config.json` sitting in the repo checkout
was read instead of the actual test installation's own file, and was
removed).

This is unchanged as a **product** decision from before this phase:
`config.json`'s `license_key`/`company_id`/`modules` fields are plain,
unsigned, locally-editable JSON with no server-side verification. Any
moderately technical customer can edit this file directly. That is
**explicitly acceptable for a genuinely offline, local-first pilot** (no
phone-home requirement is a selling point) and **explicitly not** a revenue
or access-control guarantee for a commercial release that depends on
license enforcement holding up. This limitation is unchanged by Phase 1 —
building real signed/server-verified licensing is future-phase work, not
security remediation.

## 9. Windows / Android compatibility notes

- Every new module (`core/security/passwords.py`, `app_secret.py`,
  `modes.py`, `audit.py`) imports **only** the Python standard library
  (`hashlib`, `hmac`, `secrets`, `base64`, `re`, `os`, `sys`, `stat`,
  `logging`) — verified by grepping their import statements. No change to
  `requirements.txt` or `android/app/build.gradle`'s Chaquopy `pip {}`
  block was needed.
- `aura_enterprise.spec` already does `collect_submodules('core')`
  (line 157) — the new `core.security.*` modules are picked up automatically
  by the existing PyInstaller build with no spec-file change.
- `android/app/build.gradle`'s `stageSharedPython` task already stages the
  entire `core` package (`sharedPyPackages = ['api','core','database']`,
  build.gradle:16-19) into the Chaquopy Python source set — the new files
  ship to Android automatically, same as any other `core/` module.
- Verified end-to-end via `aura_core.init_app()` (the exact function both
  `launcher.py` — Windows — and `android/.../main.py` — via `aura_core.run_server`
  — call) booting cleanly with all 68+ blueprints registered, in this
  environment, on the project's `.venv` Python 3.11 interpreter. **Not**
  independently verified: an actual Android/Chaquopy build and on-device
  run (no Android SDK/emulator available in this environment) — see the
  completion report's Build Validation section for what remains
  unconfirmed.

## 10. Test commands

```
# Full Phase 1 security suite (51 tests)
.venv\Scripts\python.exe -m pytest tests\retail_security_test.py -v

# Pre-existing regression suite (unmodified by this phase, run to confirm no breakage)
.venv\Scripts\python.exe tests\hr_smoke_test.py
.venv\Scripts\python.exe tests\crm_foundation_smoke_test.py
.venv\Scripts\python.exe tests\crm_lead_management_smoke_test.py
```

## 11. Files changed

See the completion report delivered alongside this document for the full
file-by-file list with purpose and key modifications.
