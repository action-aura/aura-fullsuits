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
# Also run (added later, see the dated comments below): crash-report pruning,
# `commercial housekeeping`, and `leads purge-website` (24-month deletion of
# unconverted website demo requests).
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
# SYNC-JOIN (2026-10-13): DRY RUN ONLY until new devices can bootstrap.
# Measured on a real three-device run (owner/tests/
# test_sync_late_join_after_prune_cross_stack.py): the prune deletes every
# event the devices that EXIST tonight have acked; a device activated later
# pulls from seq 0 and finds the shop's earlier products, customers and sales
# gone (15 events -> 1; late joiner got 0 of 2 products). The dry run still
# reports counts and fires the storage alarms. Re-enable --apply only after a
# snapshot/bootstrap path exists (ROADMAP "SYNC-JOIN").
run_job "sync prune-events (dry-run; SYNC-JOIN gap)" sync prune-events
# Added 2026-10-10: cleanups that had a function but no caller. Crash reports
# keep 90 days; `commercial housekeeping` expires stale SENT quotes and purges
# expired replay nonces / idempotency records / old rate-limit counters.
run_job "crash-reports prune --apply" crash-reports prune --apply
run_job "commercial housekeeping --apply" commercial housekeeping --apply
# Added 2026-10-25 (Step 0 fix round): a refund's licence stop ("keeps working
# 2 days, then stops") and a pair refund's device removal are stored moments;
# the shop's own check-in, activation and download apply them for that licence,
# this applies the rest -- a shop that never checks in again would otherwise
# stay ISSUED on every Owner screen. Idempotent (applied_at, once per stop).
run_job "commercial apply-licence-stops --apply" commercial apply-licence-stops --apply
# Added 2026-10-21: the website privacy page promises demo requests are deleted
# after 24 months, including the copy the website intake puts in the CRM. Only
# unconverted WEBSITE leads; a lead that became a customer or a quote is kept.
run_job "leads purge-website --apply" leads purge-website --apply
# Added 2026-11-01 (feat/customer-notices). Shop agreement cl. 4 promises that an account nobody
# activates starts its 7-day trial by itself 14 days after it was made; the command existed
# (commercial start-overdue-trials, idempotent) but nothing ever ran it.
run_job "commercial start-overdue-trials" commercial start-overdue-trials
# Same date: the privacy notice promises ID / registration photos are deleted a year after
# the customer (or the seller) is gone. This sets the dates, deletes what is due (audited)
# and unlinks purged customers from their trial-device marks. Idempotent.
run_job "documents purge-due" documents purge-due

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
