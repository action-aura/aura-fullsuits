#!/usr/bin/env bash
# AUDIT 2026-09-24 gap G2 -- the Oracle Free path's scheduled Owner
# operations. Runs the CLI commands that already exist and are already
# idempotent (owner/app/cli.py) inside the ALREADY-RUNNING `owner` container
# via `docker compose exec`, exactly the way bootstrap.sh's own step 6
# ('flask commercial preflight') already does -- no new volume mount, no
# in-container path to keep in sync with wherever this repo happens to be
# cloned, and no second Python environment to maintain on the host.
#
# Deliberately a bash wrapper, not a Python one (unlike
# deploy/staging/run_scheduled_ops.py, which invokes `docker compose run
# --rm owner python <path-inside-the-image>` -- that only works because the
# staging image bakes `deploy/staging/` into it at a fixed path; the
# oracle-free image (owner/Dockerfile.staging) only COPYs owner/, and this
# script deliberately avoids depending on a new bind mount or a rebuild to
# reach it).
#
# What this covers, and why these five and not more: the real gap the audit
# named -- expiry scan, device-limit scan, report snapshots, and
# sync-event pruning run on NEITHER free path today. `reconcile` is added
# alongside them because it is the fourth member of the SAME already-proven
# bundle deploy/staging/run_scheduled_ops.py already runs nightly (expiry-
# scan, reconcile, device-limit-scan) -- leaving it out here would make the
# Oracle path a strict regression from staging's own bundle for no reason.
#
# NOT covered, and still a regression from that staging bundle: its
# audit-chain verification (app.audit.services.verify_chain). There is no
# `flask` command for it -- staging calls it from Python in-process -- so it
# cannot be added here without either a new CLI command or an untested
# `python -c` one-liner. Recorded as an open gap in docs/ops/
# free-hosting-owner.md section 4.7 rather than papered over.
#
# Concurrency: `sync prune-events` and `reports generate-scheduled` both
# refuse outright unless OWNER_SCHEDULER_ROLE=owner in the container's own
# environment (app/scheduling.py::require_scheduler_owner) -- already set in
# .env.example for this path. The other three scans are idempotent by
# construction (dedup keys / unique constraints on what they write, per
# their own docstrings in owner/app/cli.py), so this script does not add a
# second locking layer on top -- unlike deploy/staging/run_scheduled_ops.py,
# there is exactly one host on the Oracle Free path, not a fleet, and
# systemd's own Type=oneshot semantics already refuse to start a second
# instance of an already-active unit.
#
# Logs clearly (one line per job, OK/FAILED with its exit code) and exits
# nonzero if ANY job failed -- but always runs every job regardless of an
# earlier one's failure, so one stuck scan can never hide the rest.
set -uo pipefail  # deliberately NOT -e: one job's failure must not skip the rest

# Resolves to this script's own directory regardless of $REPO_DIR / where the
# repo was actually cloned -- this file always lives next to
# docker-compose.yml and .env.oracle, so no path needs to be passed in or
# templated into this script itself (only the systemd unit that invokes it
# needs to know an absolute path -- see systemd/aura-owner-scheduled-ops.service).
COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$COMPOSE_DIR/.env.oracle"
COMPOSE=(docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$ENV_FILE")

env_value() { grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2-; }

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

overall_ok=0

run_job() {
    # $1 = human label for the log line, remaining args = the flask command.
    local label="$1"
    shift
    log "Running: $label"
    if "${COMPOSE[@]}" exec -T owner flask --app app:create_app "$@"; then
        log "OK: $label"
    else
        local code=$?
        log "FAILED (exit $code): $label"
        overall_ok=1
    fi
}

log "Starting Oracle Free scheduled ops (compose dir: $COMPOSE_DIR)"

run_job "commercial expiry-scan --apply" commercial expiry-scan --apply
run_job "commercial reconcile --apply" commercial reconcile --apply
run_job "commercial device-limit-scan --apply" commercial device-limit-scan --apply
run_job "sync prune-events --apply" sync prune-events --apply
# Added 2026-10-10: cleanups that had a function but no caller. Crash reports
# keep 90 days; `commercial housekeeping` expires stale SENT quotes and purges
# expired replay nonces / idempotency records / old rate-limit counters.
run_job "crash-reports prune --apply" crash-reports prune --apply
run_job "commercial housekeeping --apply" commercial housekeeping --apply

report_currency="$(env_value REPORT_CURRENCY)"
if [ -z "$report_currency" ]; then
    log "FAILED (not run): reports generate-scheduled -- REPORT_CURRENCY is empty/unset in $ENV_FILE"
    overall_ok=1
else
    # All three period types, every night. generate_snapshot() is idempotent
    # per (report type, period, currency) -- app/operational_reports/
    # scheduler.py returns the existing PUBLISHED row -- so weekly/monthly
    # create their snapshot on the first run after the period completes and
    # are a cheap no-op on every other night. Running only `daily` (as this
    # script first did) meant WEEKLY_/MONTHLY_OPERATIONAL_SUMMARY were never
    # generated at all. Three literal lines rather than a loop on purpose:
    # owner/tests/test_oracle_free_scheduled_ops_units.py parses each
    # invocation's real arguments and checks all three period types appear.
    run_job "reports generate-scheduled --period-type daily --currency $report_currency" \
        reports generate-scheduled --period-type daily --currency "$report_currency"
    run_job "reports generate-scheduled --period-type weekly --currency $report_currency" \
        reports generate-scheduled --period-type weekly --currency "$report_currency"
    run_job "reports generate-scheduled --period-type monthly --currency $report_currency" \
        reports generate-scheduled --period-type monthly --currency "$report_currency"
fi

if [ "$overall_ok" -eq 0 ]; then
    log "All scheduled-ops jobs OK."
else
    log "One or more scheduled-ops jobs FAILED -- see lines above."
fi
exit "$overall_ok"
