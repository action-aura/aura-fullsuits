# Aura Owner Control Center

Internal Action Aura staff system for managing the commercial catalog, customer
organizations, subscriptions, licenses, installations, and staff RBAC. **Not
sold to customers, not bundled inside Retail or Clinic, not a customer
dashboard.** See `docs/owner/phase5/owner-foundation-scope.md` for full scope
and boundaries.

## Quick start (Docker)

```
cd owner
cp .env.example .env      # fill in OWNER_SECRET_KEY / OWNER_LICENSE_PEPPER for anything beyond local dev
docker compose up --build
```

Then, in a second terminal:

```
docker compose exec app flask create-superadmin --email you@example.com --display-name "Your Name"
```

The app is at `http://localhost:5551`.

## Quick start (local, no Docker)

Requires a local PostgreSQL 17+ instance and Python 3.11+.

**Followed verbatim, this produces a working licensing authority** -- not
just a dashboard. Two real gaps found by an actual local end-to-end run
(fresh Owner -> real licence -> fresh till -> persisted sale, 2026-09-22)
used to make that untrue even after every step below looked like it
succeeded: `flask seed-plans` was never called anywhere in this repo (so
`issue_license_direct` had no plan to issue against), and
`OWNER_EXTERNAL_API_ENABLED` defaults to `false` (so the app boots healthy,
`/healthz` returns 200, and the licensing/activation/sync API is silently
never registered -- nothing logged an error). Both are closed below.

```
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
pip install -r requirements/development.txt   # run from the aura-fullsuits repo root

createuser aura_owner --pwprompt          # set password: aura_owner_dev, or your own
createdb aura_owner_dev -O aura_owner
createdb aura_owner_test -O aura_owner

cd owner
export OWNER_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev

# HMAC pepper for license-key secret hashing (Part O). DevelopmentConfig
# already falls back to this exact insecure placeholder if unset, so this
# export is not strictly required to get a working local instance -- it is
# spelled out here so the value is visible rather than buried in
# config.py, and so `bootstrap-dev`'s pepper self-test below has a
# real, non-empty value to round-trip. Replace with a real secret for
# anything beyond throwaway local dev; config.py rejects this exact
# placeholder outside development/testing.
export OWNER_LICENSE_PEPPER=dev-only-insecure-pepper-do-not-use-in-production

# WITHOUT THIS the app still boots, /healthz still returns 200, and the
# admin dashboard still works completely -- but create_app() never
# registers the licensing/activation API (app/api_external/routes.py), the
# device-facing API (app/api/routes.py), or the sync relay (app/sync), so
# no till anywhere can activate, check in, or sync. This is the single
# most-missed step in this whole quick start; it has no default that makes
# it safe to skip.
export OWNER_EXTERNAL_API_ENABLED=true

python -m alembic upgrade head
flask --app app:create_app bootstrap-dev
```

`bootstrap-dev` (added 2026-09-22, `owner/app/cli.py`) is the one command
this quick start now points to instead of five separate steps an operator
can forget one of. It idempotently runs all four seed commands --
`seed-rbac`, `seed-catalog`, `seed-plans`, `seed-offline-policy` -- generates
and activates a signing key if none is active yet (`licensing
generate-signing-key` + `licensing activate-signing-key`), then runs `flask
commercial preflight` itself and prints the result, exiting nonzero if
preflight still fails. It cannot set `OWNER_EXTERNAL_API_ENABLED` or
`OWNER_LICENSE_PEPPER` for you -- both are read once at process boot
(`app/config.py`) -- but its final preflight report names exactly which of
those, if either, still needs fixing before you restart it. Running the four
seed commands and the two signing-key commands by hand, in this order,
does the same thing:

```
flask --app app:create_app seed-rbac
flask --app app:create_app seed-catalog
flask --app app:create_app seed-plans
flask --app app:create_app seed-offline-policy
flask --app app:create_app licensing generate-signing-key
flask --app app:create_app licensing activate-signing-key <key_id-from-above>
flask --app app:create_app commercial preflight
```

Create the first Super Admin -- deliberately its own explicit,
password-bearing step, never folded into `bootstrap-dev` (same reason
`seed-plans` is its own command rather than folded into `seed-catalog`: see
that function's own docstring) -- then start the server:

```
flask --app app:create_app create-superadmin --email you@example.com --display-name "Your Name"
flask --app app:create_app run --port 5551
```

### Verify it actually works -- do not assume

`bootstrap-dev`'s own preflight run already checked signing-key/RBAC/plan/
external-API readiness against the database. With the server running from
the step above, check the real HTTP surface it depends on:

```
curl -s http://127.0.0.1:5551/api/licensing/v1/signing-keys
```

Expected -- a signed key-set manifest, one entry per key ever activated
(trimmed; see `app/licensing_service/signing.py::export_signed_keyset_manifest`
for the full field list):

```json
{
  "schema_version": 1,
  "keys": [
    {"key_id": "...", "algorithm": "ed25519", "public_key": "...", "use": "assertion-signing", "status": "ACTIVE", "valid_from": "2026-09-22T...", "retired_at": null}
  ],
  "manifest_version": 1,
  "issued_at": "2026-09-22T...",
  "signed_by_key_id": "...",
  "signature": "...",
  "signatures": [{"key_id": "...", "signature": "..."}]
}
```

A `404` here (not an error body -- a plain 404, no route) means
`OWNER_EXTERNAL_API_ENABLED` is not `"true"` for the running process --
check its startup log for a `WARNING` naming this exact condition
(`app/__init__.py`), or rerun `flask --app app:create_app commercial
preflight`, which fails closed on it too.

Full detail (including this session's specific environment notes): `docs/owner/phase5/owner-local-development-guide.md`.

## Licensing & Activation Service (Phase 6)

The Quick start above already enables this (`OWNER_EXTERNAL_API_ENABLED=true`)
and provisions a signing key via `bootstrap-dev`. Licence issuance is a
dashboard action, not a CLI command (`issue_license_direct`, wired to the
staff-facing issuance screen) -- log in as the Super Admin created above and
issue a real test licence against the plan `bootstrap-dev` seeded. Then
drive the activation protocol against it with the standalone simulator (real
Ed25519 crypto, real HTTP, no mocks):

```
python -m owner.tools.activation_simulator init-device
python -m owner.tools.activation_simulator activate --license-key <a-real-issued-test-key>
python -m owner.tools.activation_simulator check-in
python -m owner.tools.activation_simulator verify-assertion
```

No Redis required -- replay protection, rate limiting, and idempotency are all PostgreSQL-backed. Full design docs: `docs/owner/phase6/`, starting with `PHASE6-LICENSING-ACTIVATION-HANDOVER.md`.

## Running tests

```
export OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test
python -m pytest owner/tests/ -q
```

See `docs/owner/phase5/owner-test-report.md` for the full test report.

## Staging (Phase 9)

A hardened staging deployment path exists: `owner/Dockerfile.staging` +
`docker-compose.staging.yml` (repo root), real `/health/live`/`/health/ready`,
real structured/redacted logging, a real backup+restore drill, and a real
scheduler for the commercial scan jobs. See `docs/owner/phase9/
PHASE9-SECURE-STAGING-AND-PILOT-READINESS-HANDOVER.md` for what was actually
verified (locally, natively, this machine has no cloud/VPS/Docker Engine)
versus what remains NOT VERIFIED (a real remote host, real public TLS, real
containerized deployment). As of Phase 9, staging is **not yet actually
deployed anywhere reachable** -- the capability is real and tested, the real
deployment is not.

## Running the full product test matrix (not just Owner)

`python products/run_all_tests.py` (optionally `retail`/`clinic`/
`commercial_runtime`/`licensing_contracts` to scope it) is the one supported
command for Retail/Clinic -- runs each test file in its own subprocess,
deliberately, because `config.py`/`registry_db.py`/`schema.py` resolve
`AURA_APP_DATA` once at import time (correct for a real single process, not
safe to collect many test files into one `pytest` invocation). See
`docs/owner/phase9/retail-test-isolation-root-cause.md` before "fixing" this
any other way.

## Internationalization (English/Arabic, RTL)

Phase 9.5B-R added one canonical i18n/RTL foundation (Flask-Babel). Phase 9.5B-R2 completed Owner-wide
coverage: every real current Owner template (67/67 -- layout/navigation, auth/setup/MFA, the employee
portal, and every commercial-administration screen: catalog, customers, subscriptions, licensing,
installations, commercial operations, staff, audit, system) is now translated. Real, compiled English +
Arabic catalogs (742 messages, zero empty/fuzzy) live at `translations/{en,ar}/LC_MESSAGES/messages.po`.
To add or update a translatable string:

```
python -m babel.messages.frontend extract -F babel.cfg -o translations/messages.pot .
python -m babel.messages.frontend update -i translations/messages.pot -d translations
# fill/correct English (identity) and Arabic (real translation) entries --
# check both emptiness AND the fuzzy flag; pybabel's approximate-match
# heuristic can silently pair a new string with the wrong old translation
# (see docs/owner/phase9_5b_r2/rtl-defect-and-fix-log.md item 5)
python -m babel.messages.frontend compile -d translations
```

`flask commercial preflight` validates the compiled catalogs exist, are non-empty, AND contain zero
empty/fuzzy entries (Phase 9.5B-R2 extension). See `docs/owner/phase9_5b_r2/` for the full closure
evidence, and `docs/owner/phase9_5b_r/translation-catalog-maintenance.md` /
`translation-style-guide.md` for the original maintenance workflow and terminology glossary.

Phase 9.5B-R3 closed the remaining verification gaps from R2: a nondeterministic Owner test failure
(root-caused and fixed, see `docs/owner/phase9_5b_r3/flaky-test-root-cause-and-fix.md`), executed
dependency/secret scans (`docs/owner/phase9_5b_r3/dependency-scan-final.md`,
`secret-scan-final.md`), a request-context-free stable-code architecture for `commercial_ops` service
exceptions (`docs/owner/phase9_5b_r3/service-error-architecture-result.md` -- raise
`SomeError("CODE", **params)` in service code, never `_()`; localize only in
`commercial_ops/ui_routes.py` via `localize_*_error()`), and complete browser/keyboard-focus
validation across all 16 current route families (`docs/owner/phase9_5b_r3/
complete-browser-family-validation.md`). Full closure record and final verdict:
`docs/owner/phase9_5b_r3/PHASE9-5B-R3-FINAL-VERIFICATION-HANDOVER.md`.

## Expense Management, Cash Control, Reporting & Management Collaboration (Phase 9.5E)

Phase 9.5D added the commercial sales path (Lead→Quote→Order→Invoice→Payment→
Refund→Commission); Phase 9.5E adds the operational-finance side: Expense
lifecycle/approval/payments/attachments/duplicate-review (`app/expenses/`),
Daily Cash Closing with an authoritative formula and MFA-gated reopen
(`app/cash_closing/`), read-only operational aggregation + 3 role-scoped
dashboards + idempotent scheduled report snapshots (`app/operational_reports/`),
and the first real service layer over the Phase 9.5A `SharedManagementNote`
schema (`app/management_notes/`). A 35-route `/api/operations/v1` API and a
32-route web UI (11 templates) sit on top, fully EN/AR localized. Full
closure record: `docs/owner/phase9_5e/PHASE9-5E-EXPENSES-REPORTING-HANDOVER.md`.

`flask commercial preflight` remains the single blocking gate -- Phase 9.5E
extends it (14 operational-finance sub-checks), it does not add a second
command.

## What this is NOT

Not connected to Retail or Clinic. Not enforcing any license inside either
product. Not internet-reachable. Not collecting telemetry. Not deployed to
any real remote host as of Phase 9 (see Staging section above -- the
capability exists, the real deployment does not). See
`docs/owner/phase5/owner-foundation-scope.md` and
`docs/owner/phase5/owner-residual-risk-register.md` for the original Phase 5
scope this statement is inherited from.
