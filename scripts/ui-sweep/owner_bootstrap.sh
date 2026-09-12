#!/bin/sh
# Stand up a THROWAWAY Owner database for a UI sweep.
#
# Deliberately its own database (aura_owner_uishots) rather than
# aura_owner_dev: that one carries the demo customer, subscriptions and the
# live licensing signing key, and this session has no business writing to it
# just to take screenshots.
set -e

PSQL="C:/pgportable/pgsql/bin/psql.exe"
PY="C:/Users/MSI/Desktop/aura-fullsuits/.venv-owner/Scripts/python.exe"
DB=aura_owner_uishots

export PGPASSWORD=aura_owner_dev
export OWNER_DATABASE_URL="postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/${DB}"
export OWNER_SECRET_KEY="uishots-local-only-not-for-production"
export OWNER_LICENSE_PEPPER="uishots-local-only-pepper"
export OWNER_EXTERNAL_API_ENABLED=true
export FLASK_APP="app:create_app"

echo "=== (re)create ${DB} ==="
"$PSQL" -h localhost -p 5432 -U aura_owner -d postgres -c "DROP DATABASE IF EXISTS ${DB};" >/dev/null
"$PSQL" -h localhost -p 5432 -U aura_owner -d postgres -c "CREATE DATABASE ${DB};" >/dev/null
echo "created"

cd owner

echo "=== alembic upgrade head ==="
"$PY" -m alembic upgrade head 2>&1 | tail -4

echo "=== seed-rbac ==="
"$PY" -m flask seed-rbac 2>&1 | tail -3

echo "=== seed-catalog ==="
"$PY" -m flask seed-catalog 2>&1 | tail -3

echo "=== create-superadmin ==="
OWNER_BOOTSTRAP_PASSWORD='UiShots2026!Pass' "$PY" -m flask create-superadmin \
  --email uishots@aura.local --display-name "UI Shots" --non-interactive 2>&1 | tail -3

echo "=== done ==="
