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

```
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
pip install -r requirements/development.txt   # run from the aura-fullsuits repo root

createuser aura_owner --pwprompt          # set password: aura_owner_dev, or your own
createdb aura_owner_dev -O aura_owner
createdb aura_owner_test -O aura_owner

cd owner
export OWNER_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev
python -m alembic upgrade head
flask --app app:create_app seed-rbac
flask --app app:create_app seed-catalog
flask --app app:create_app seed-offline-policy
flask --app app:create_app create-superadmin --email you@example.com --display-name "Your Name"
flask --app app:create_app run --port 5551
```

Full detail (including this session's specific environment notes): `docs/owner/phase5/owner-local-development-guide.md`.

## Licensing & Activation Service (Phase 6)

Disabled by default. To exercise it locally:

```
export OWNER_EXTERNAL_API_ENABLED=true
flask --app app:create_app licensing generate-signing-key
flask --app app:create_app licensing activate-signing-key <key_id-from-above>
flask --app app:create_app run --port 5551
```

Then drive it with the standalone simulator (real Ed25519 crypto, real HTTP, no mocks):

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

## What this is NOT

Not connected to Retail or Clinic. Not enforcing any license inside either
product. Not internet-reachable. Not collecting telemetry. Not deployed to
any real remote host as of Phase 9 (see Staging section above -- the
capability exists, the real deployment does not). See
`docs/owner/phase5/owner-foundation-scope.md` and
`docs/owner/phase5/owner-residual-risk-register.md` for the original Phase 5
scope this statement is inherited from.
