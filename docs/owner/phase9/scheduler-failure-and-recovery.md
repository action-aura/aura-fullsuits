# Phase 9 — Scheduler Failure and Recovery

## A single job fails (e.g. `device-limit-scan` errors)

`run_scheduled_ops.py` still runs every remaining job in the bundle (one job's failure does not skip
the rest — real, tested behavior: `overall_ok = all(r["ok"] for r in results)` is computed only after
every job has already run). Exit code `1`. Real deployment: `systemd`'s `OnFailure=` hook (documented,
not wired to a real alert channel this session — no real host) fires the alert-catalog's
"Reconciliation scan failure" row. Manual recovery: re-run the single failed command directly
(`flask commercial device-limit-scan --apply`) once the underlying cause is fixed — every command is
independently safe to re-run (idempotent, dedup-protected).

## The whole run times out or crashes mid-way

The Postgres advisory lock is released automatically the moment that process's DB connection closes —
whether via the `finally` block on a clean exit, or via Postgres's own connection-loss cleanup on a
hard crash/OOM-kill. No manual lock-clearing step exists or is needed (deliberately — a manual
"force-unlock" command would reintroduce exactly the double-run risk the lock exists to prevent). The
next scheduled run (or a manual re-run) proceeds normally once the crashed process's connection has
actually closed (typically immediate; `RandomizedDelaySec=300` on the real systemd timer also naturally
absorbs this).

## Two runs overlap (e.g. a manual run during the scheduled window)

By design: the second acquirer sees `{"status": "SKIPPED", ...}`, exit `0` — not an error, not paged.
Confirmed via two real concurrent invocations this session.

## Backup job fails

Highest-severity row in `alert-catalog.md` (P0) — a failed backup with no successful backup in the
last 26 hours means the RPO target is at risk. Manual recovery: re-run `docker compose -f
docker-compose.staging.yml run --rm backup` directly; investigate disk space / DB connectivity first
(the two most likely real causes) via `/health/ready` and `df -h` on the host.

## Audit-chain verification fails

Treated as the single most serious possible finding from this bundle (P0 in `alert-catalog.md`) — a
broken hash chain means either real data corruption or a real tampering attempt. Do not re-run the
scan to "fix" it. Follow `incident-response-plan.md`'s "suspicious login"/"compromised" categories'
evidence-preservation steps before taking any further action against the database.
