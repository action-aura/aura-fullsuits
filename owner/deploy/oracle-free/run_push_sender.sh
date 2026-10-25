#!/usr/bin/env bash
# Step 1 (2026-10-25) -- the backstop for manager push notifications.
#
# The web process already sends them: app/push/worker.py runs a background
# thread in every gunicorn worker (a tick every 10 s) whenever
# OWNER_PUSH_VAPID_PRIVATE_KEY is set, the same way Owner's email queue is
# sent (app/mail/worker.py). This script is the second sender, run every
# minute by systemd/aura-owner-push.timer, for when no web process is up to
# do it (a restart, a crashed thread). Both go through the same claims (a
# unique row per sale and device, a lease per device), so running both never
# sends one sale to one phone twice.
#
# Same mechanism as run_scheduled_ops.sh: the CLI inside the ALREADY-RUNNING
# `owner` container via `docker compose exec`. Prints nothing when there was
# nothing to send (--quiet-when-idle), so the journal holds only real work.
# Installed by bootstrap.sh only when .env.oracle has a VAPID key -- with push
# off there is nothing to send and a timer would be noise.
set -uo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$COMPOSE_DIR/.env.oracle"

exec docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE" \
    exec -T owner flask --app app:create_app notifications send-pending --quiet-when-idle
