# Per-Device Login / "One Admin Device" — Phase 1 Implementation Plan

> Retrospective plan doc: written after the fact to record what Stream C
> actually built each day this week, not a pre-implementation task list.
> Companion to docs/superpowers/specs/2026-08-10-per-device-login.md (the
> Phase 0 decision record — read that first for the *why* behind the
> `company_id`-authority and per-registry.db-scope decisions referenced
> below).

**Goal:** Backend-only, schema-and-visibility groundwork for per-device
login (company-scoped accounts, device-scoped authorization — spec's
Reading B) without touching any live login path. Enforcement stays
default-OFF all week.

**Scope guardrail (held all three days):** `commercial_runtime/identity/`
started this week with zero diff against `master`, and this stream never
touched `mt_auth.py`, `auth_routes.py`, `onboarding_routes.py`,
`licensing_contracts/**`, either product's `config.py`, `retail_api.py`,
`commercial_runtime/sync/**`, or `owner/**`.

---

## Monday — `device_registry.py` + `registry.db` migration

**Built:** `commercial_runtime/identity/device_registry.py` — the
`devices` / `user_devices` schema (`apply_identity_device_schema()`) plus
CRUD: `upsert_local_device`, `get_device`, `list_devices`,
`set_admin_device`, `revoke_device`, `grant_user_device`,
`revoke_user_device`, `allowed_on_device`. Every function takes an explicit
`conn` — no module-level DB_PATH, no import-time filesystem access; callers
(`registry_db.get_conn()`) own connection lifecycle.

The "one Admin Device" invariant (`idx_devices_one_admin`, a partial unique
index on `devices(company_id) WHERE is_admin_device = 1`) is enforced by
the database itself, not application code — `set_admin_device()` clears the
whole company's flag before setting the new holder specifically so the
partial-index predicate is never transiently satisfied by two rows at once.

**Wired:** `commercial_runtime/identity/registry_db.py` — `registry.db` had
no `PRAGMA user_version` at all before this; `REGISTRY_SCHEMA_VERSION = 1`
is the first version ever applied to it, via
`commercial_runtime.security.migration_safety.ensure_schema_version()` (live
backup + `PRAGMA integrity_check` before and after, version only advances on
full success — the same paranoid pattern already used for
retail.db/clinic.db).

**Tests:** `commercial_runtime/identity/tests/test_device_registry.py` —
schema creation + idempotency, the partial-unique-index invariant (both
raw-SQL and via `set_admin_device`), the grant lifecycle, `revoke_device`'s
audit-trail preservation, fingerprint-unique-index NULL handling. Plus
registry.db migration-safety coverage (`commercial_runtime/tests/`).

---

## Tuesday — `device_context.py`

**Built:** `commercial_runtime/identity/device_context.py` —

- `local_device_fingerprint()`: reads the licensing system's own
  `device_key_meta.json` as plain JSON (no import of
  `licensing_contracts.device_identity`, no `cryptography` anywhere in this
  module — Android/Chaquopy safety constraint, guarded by
  `test_local_device_fingerprint_reads_without_importing_cryptography`, the
  regression test for the exact `ModuleNotFoundError('cryptography')` crash
  that previously broke `app.py`'s import on Android).
- `local_device_uuid()`: fallback local device identity for installs with
  no licensing configured, persisted once to `device/local_device.json`,
  refuses to silently regenerate on corrupt state
  (`LocalDeviceStateCorruptError`).
- `resolve_local_device(conn, company_id, device_label=None, platform=None)`:
  idempotent per-install resolution — row `id` is always the local UUID
  (never the fingerprint, so a device that resolves once pre-activation and
  again post-activation updates its existing row instead of duplicating),
  process-cached, and implements Phase 0 decision 0-a: refuses to silently
  rebind a device already registered under a different `company_id`
  (`DeviceCompanyMismatchError`) rather than overwriting it.
- `binding_enforced()`: reads `AURA_DEVICE_BINDING_ENFORCED`, defaults to
  `False`. No caller yet — see Residual gaps.

**Tests:** `commercial_runtime/identity/tests/test_device_context.py` —
fingerprint read path (including the cryptography-import regression guard
above), UUID fallback lifecycle, `resolve_local_device`'s update-not-duplicate
behavior, the company_id-mismatch refusal, process-level caching, and
`binding_enforced()`'s truthy/falsy parsing.

---

## Wednesday — `device_routes.py` + blueprint registration

**Built:** `commercial_runtime/identity/device_routes.py` — two read-only
GET endpoints, no mutation routes, no wiring into login:

- `GET /api/devices` (`@mt_login_required`) — `device_registry.list_devices(conn, company_id)`
  for the session's `company_id`, scoped, JSON envelope
  `{"success": true, "devices": [...]}`.
- `GET /api/devices/me` (`@mt_login_required`) — `device_context.resolve_local_device(conn, company_id)`,
  JSON envelope `{"success": true, "device": {...}}`, with `is_admin_device`
  explicitly cast to a real JSON bool (not SQLite's stored 0/1) — deliberate,
  so a future notification worker can read it directly to self-gate which
  device sends scheduled reports without re-deriving anything.

Plain module-level `Blueprint('devices', __name__, url_prefix='/api/devices')`,
matching `auth_bp`/`onboarding_bp`'s structural precedent in this same
package (no factory function needed — no per-product config, resolves its
own connection via `registry_db.get_conn()`), not the factory-style
`make_backup_blueprint`/`make_licensing_blueprint` pattern used by blueprints
that genuinely need per-product config.

Verified cryptography-clean by import-time `sys.modules` delta (mirrors
`test_local_device_fingerprint_reads_without_importing_cryptography`'s
technique) — importing `device_routes` pulls in no `cryptography` module and
nothing under `licensing_contracts`.

**Wired:** one `app.register_blueprint(device_bp)` line (plus its import)
in each of `products/retail/backend/app.py` and
`products/clinic/backend/app.py`, placed immediately after the existing
`make_backup_blueprint(...)` registration and *before* each file's "Part H"
lazy `cryptography`/`WindowsDpapiDeviceIdentityProvider` import block —
deliberately, so this addition can never be blamed for forcing that import
eager.

**Tests:** `commercial_runtime/identity/tests/test_device_routes.py` — real
Flask test client against the blueprint (same spirit as
`commercial_runtime/sync/tests/test_internal_routes.py`), covering
auth-required 401 rejection on both routes, the `/api/devices` response
shape and company_id scoping, and the `/api/devices/me` response shape
including the `is_admin_device` bool-not-int assertion in both the false
and true cases.

**Documented:** `AURA_DEVICE_BINDING_ENFORCED` added to root `.env.example`
under the Retail/Clinic section, explicitly noted as a currently-no-op flag
(no caller yet).

---

## Residual gaps (explicitly out of scope this week, not silently dropped)

- **Login enforcement is not wired.** `mt_auth.py`, `auth_routes.py`, and
  `onboarding_routes.py` are byte-for-byte untouched this week.
  `device_context.binding_enforced()` exists and is tested but has **no
  caller anywhere in the codebase** — setting `AURA_DEVICE_BINDING_ENFORCED=1`
  today changes nothing about who can log in from where. Wiring that gate
  into the actual login path is explicitly future work, requiring its own
  review.
- **"One Admin Device" is per-`registry.db` only, not cross-device.** The
  partial unique index enforces at most one admin device *inside a single
  install's database*. The sync outbox
  (`commercial_runtime/sync/`, `sync_outbox`/`sync_cursor`) does not carry
  `devices`/`user_devices` rows, admin-flag changes, or grants at all today
  — only `category`/`product`/`customer`/`supplier` events. Consequently,
  **two physical devices on the same `company_id` could each independently
  and validly believe they are the admin device**, each locally consistent,
  mutually contradictory across machines. This is a known Phase 1
  limitation by design (see spec §c), not a bug — closing it requires either
  extending the sync outbox to carry device-state events or routing
  admin-device decisions through Owner as an external authority, both out of
  scope this week.
- `company_id` authority itself is still the pre-existing
  `create_admin`-time value (`md5(email)` or `config.json`), unchanged —
  Phase 0 decision 0-a only makes drift *detectable and loud*
  (`DeviceCompanyMismatchError`), it does not fix the root cause. Options
  0-b (derive `company_id` from licensing/installation id) and 0-c ("join
  existing installation" onboarding flow) remain deferred, pending
  sync-stream owner sign-off per the spec.
- No admin-facing UI consumes `GET /api/devices` / `GET /api/devices/me`
  yet — these are backend visibility endpoints only.
