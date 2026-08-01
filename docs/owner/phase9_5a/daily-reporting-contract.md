# Phase 9.5A Milestone 15 — Daily Reporting Contract

## New: `daily_activity_snapshots` (`owner/app/models/daily_reports.py`)

```
id, business_date (date, unique — one snapshot per business date, enforced by DB UNIQUE),
timezone (fixed "Asia/Amman", stored for explicitness not variability),
generation_status (PENDING|GENERATING|COMPLETE|FAILED), generated_at (timestamptz, nullable),
generated_by (SCHEDULER|MANUAL, plus staff_user_id nullable when MANUAL),
source_range_start/end (timestamptz — the exact UTC window the business_date resolved to),
metric_payload (JSONB — the full metric catalog, see `daily-snapshot-metric-catalog.md`),
schema_version (int, for forward-compatible payload evolution), failure_reason (nullable text),
rerun_count (int, default 0), created_at, updated_at
```

## Generation mechanics — reuses the real Phase 9 scheduler, not a new one

A new job, `daily-snapshot-generate`, added to `flask commercial` CLI group (Milestone 22 — a thin
wrapper calling `DailySnapshotService.generate(business_date)`), wired into
`deploy/staging/run_scheduled_ops.py`'s existing job bundle at `00:00 Asia/Amman` (converted to the
real UTC cron-equivalent trigger the existing systemd timer already fires on — Phase 9's own
`aura-owner-scheduled-ops.timer` already runs at `03:00` UTC; the daily-snapshot job specifically needs
midnight *Amman* time, `21:00 UTC` in winter / `21:00 UTC` in summer since Jordan does not observe DST —
a real, distinct trigger time, wired as its own line item in the job bundle, not assumed to align with
the existing `03:00` scan-bundle timer).

## Idempotency, concurrency, locking — reuses the real Milestone 9 (Phase 9) mechanism

Same Postgres-advisory-lock pattern as `run_scheduled_ops.py` (a distinct lock key,
`DAILY_SNAPSHOT_LOCK_KEY`, so a snapshot run and a scan-bundle run can proceed concurrently without
contending for the same lock). `generate(business_date)` is idempotent per `business_date` via the
table's own `UNIQUE (business_date)` — a second real-time attempt for a date that already has a
`COMPLETE` row updates nothing unless `force_regenerate=True` is explicitly passed (the manual-rerun
path), which increments `rerun_count` and re-runs the full computation rather than silently no-op'ing.

## Late-event handling policy (explicit, since the spec requires a documented choice)

An event that lands after midnight but logically belongs to the prior business date (e.g. a payment
webhook — not applicable here, no gateway — or a delayed batch write) is **not** auto-detected or
auto-included. Real policy: any authorized manual regeneration (`reports.regenerate_daily` permission)
within a bounded window (e.g. 48 hours) recomputes that date's snapshot from scratch using the same
real query logic, correctly picking up anything that landed late — this is the entire mechanism; no
separate "late event queue" is built.

## No customer-domain Clinic/Retail data, no secrets

Every metric in the catalog is derived from Owner's own tables only (leads, customers, quotes, orders,
invoices, payments, subscriptions, licenses, installations, commissions, expenses, employees) —
structurally impossible to include Clinic/Retail business data, since Owner has none (unchanged
Non-Negotiable boundary, Phase 8/9).

## Employee vs management visibility

`GET /api/operations/v1/reports/daily/{business_date}` returns the employee's own-scoped subset
(their own leads/follow-ups/sales/commissions only) for `reports.view_own`; the full global payload
requires `reports.view_all`. This is a real, server-side response-shaping rule, not two different
snapshot rows — one `metric_payload` is generated, and per-employee scoping is applied at serialization
time by filtering the already-computed per-employee breakdown section of the payload (see the catalog
doc's `by_employee` structure).
