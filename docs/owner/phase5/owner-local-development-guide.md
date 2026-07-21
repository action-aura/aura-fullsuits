# Phase 5 -- Owner Local Development Guide

## What this sandbox actually had, and what was set up
This session's sandbox had neither Docker nor PostgreSQL pre-installed. Rather than substitute a weaker stand-in (e.g. SQLite, contradicting Principle 8), the real dependencies were installed:
- `winget install --id PostgreSQL.PostgreSQL.17 --silent` -- installs PostgreSQL 17 as a Windows service, listening on `localhost:5432`.
- A Python virtual environment at `aura-fullsuits/.venv`, `pip install -r requirements/development.txt` plus `pyotp`, `flask-wtf`, `jsonschema`, `cryptography` (added to `requirements/owner-server.txt` this phase -- see `owner-architecture-decision-record.md` ADR-4).
- Postgres role/databases: `CREATE ROLE aura_owner LOGIN PASSWORD 'aura_owner_dev' CREATEDB;` (CREATEDB needed for the migration-scratch-database test, `test_migration_runs_clean_from_empty_database_and_rolls_back`), `aura_owner_dev` and `aura_owner_test` databases, both owned by `aura_owner`.

`docker-compose.yml`/`Dockerfile` are still the primary, portable path documented in `owner/README.md` for anyone with Docker available -- the above is this specific sandbox's substitute, disclosed honestly rather than silently assumed.

## Day-to-day commands (from `aura-fullsuits/owner/`)
```
# Apply migrations
OWNER_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev python -m alembic upgrade head

# Seed canonical RBAC + catalog (idempotent, no fake data)
flask --app app:create_app seed-rbac
flask --app app:create_app seed-catalog

# Bootstrap the first Super Admin (interactive password prompt)
flask --app app:create_app create-superadmin --email you@example.com --display-name "Your Name"

# Run the dev server
flask --app app:create_app run --port 5551

# Run the test suite (separate aura_owner_test database)
OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test python -m pytest owner/tests/ -q
```

## Resetting a database to empty
```
OWNER_DATABASE_URL=<target> python -m alembic downgrade base
OWNER_DATABASE_URL=<target> python -m alembic upgrade head
```

## Current state of the dev database as left at the end of this phase
`aura_owner_dev` contains **only** the canonical RBAC (46 permissions, 5 roles) and catalog seed (2 products, 2 platforms, 4 release channels, 10 entitlement definitions, 9 DRAFT/PLANNED add-ons, 4 product-platform mappings) -- **no** staff account, **no** fake customer/subscription/license/installation data, per Part AA's explicit seed-data rules. A full end-to-end manual verification (customer -> plan -> subscription -> license -> issued key -> installation) was performed live against this database during this phase and then explicitly reset (`alembic downgrade base` + `upgrade head` + re-seed) to leave it in this canonical state -- see `owner-test-report.md` for that verification's results.
