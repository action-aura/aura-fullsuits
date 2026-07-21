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

## What this is NOT

Not connected to Retail or Clinic. Not enforcing any license inside either
product. Not deployed anywhere. Not internet-reachable. Not collecting
telemetry. See `docs/owner/phase5/owner-foundation-scope.md` and
`docs/owner/phase5/owner-residual-risk-register.md`.
