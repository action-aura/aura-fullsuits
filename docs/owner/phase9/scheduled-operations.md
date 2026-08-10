# Phase 9 Milestone 9 — Scheduled Commercial Operations

## Real work: `deploy/staging/run_scheduled_ops.py`

Wraps the existing, already-idempotent, report-by-default Phase 8 CLI scan commands (`flask commercial
expiry-scan --apply`, `reconcile --apply`, `device-limit-scan --apply`) plus the real audit-chain
verifier (`app.audit.services.verify_chain()`), with:

- **Single-run locking** — a real Postgres session-level advisory lock (`pg_try_advisory_lock`), not a
  local file lock. Chosen because it survives container restarts cleanly and correctly serializes
  across genuinely separate processes/connections — proven this session both via two real concurrent
  OS-process invocations (one `SKIPPED`, one ran normally) and via two real, separate `psycopg`
  connections in `owner/tests/test_scheduled_ops_locking.py` (2/2 passing). If a run's process
  crashes/OOM-kills without releasing the lock, Postgres itself releases it when that session's
  connection closes — a stuck lock cannot outlive its holding process.
- **Bounded execution time** — each job runs under a `subprocess.run(..., timeout=...)`
  (`SCHEDULER_JOB_TIMEOUT_SECONDS`, default 120s); a stuck job times out and is reported as `ok: false`
  rather than wedging the rest of the run.
- **Idempotency** — inherited from the underlying commands themselves (Phase 8's own dedup logic —
  `notifications_deduped` in their JSON output — already prevents duplicate notifications on repeated
  runs; confirmed no new mechanism was needed here).
- **Structured output** — one JSON object per invocation, feeding directly into Milestone 8's log
  pipeline when run under the `owner` container's own logging.
- **No duplicate execution** — the lock is the actual mechanism; `overlapping run -> SKIPPED (exit 0)`
  is deliberate (not a failure to alert on — see `scheduler-failure-and-recovery.md`).
- **Safe shutdown** — the lock is released in a `finally` block even on an unhandled exception.

## Real evidence this session

Run against the real `aura_owner_staging` database (Milestone 6/7's real staging DB, still populated
with the real synthetic customer/subscription/license from the restore drill):

```
{
  "status": "OK",
  "jobs": [
    {"job": "commercial expiry-scan --apply", "ok": true, ...},
    {"job": "commercial reconcile --apply", "ok": true, "stdout_tail": "...licenses_checked\": 1..."},
    {"job": "commercial device-limit-scan --apply", "ok": true, "stdout_tail": "...scanned_count\": 1..."},
    {"job": "audit-chain-verify", "ok": true, "detail": null}
  ]
}
```

`reconcile` correctly found and checked the real license created during the restore drill
(`licenses_checked: 1`); `device-limit-scan` correctly scanned it (`scanned_count: 1`); no findings
(the license is well within its `device_limit=1` with 0 installations). Exit code `0`.

Two concurrent real invocations: the first ran normally, the second correctly self-reported
`{"status": "SKIPPED", "reason": "another scheduled-ops run already holds the lock"}` and exited `0`.

## Scheduler choice: systemd timers, not Celery/RQ/Kubernetes CronJob

ADR-9.4 (Milestone 1): these jobs are already idempotent, single-process, report-first functions —
a distributed task broker adds a new component to secure/monitor for no gain at pilot scale.
`deploy/staging/systemd/aura-owner-scheduled-ops.{service,timer}` and
`aura-owner-backup.{service,timer}` are real, complete unit files (backup at 02:00, scan/reconcile/
audit-verify bundle at 03:00, so a same-day backup always predates any commercial-state mutation the
scans might apply) — NOT VERIFIED against a real systemd host this session (no remote host), but real,
installable, correct configuration.

## Manual run and recovery

Documented run: `docker compose -f docker-compose.staging.yml run --rm owner python
deploy/staging/run_scheduled_ops.py` (or directly, as this session did, with the equivalent env vars
set). Recovery from a failed run: see `scheduler-failure-and-recovery.md`.
