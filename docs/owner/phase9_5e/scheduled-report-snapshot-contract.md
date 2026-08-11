# Phase 9.5E — Scheduled Report Snapshot Contract

Binding rules for `app/operational_reports/scheduler.py` + `app/models/report_snapshots.py`.

## Why in-process, not a second OS-level scheduler

Milestone 1's duplication-risk report reasoned this explicitly: this codebase has no scheduler dependency today (no Celery/APScheduler), and Milestone 0 just spent its entire effort auditing the blast radius of exactly one OS-level Scheduled Task (`.autosync`). Adding a second one for report generation would repeat that risk class for no proven need — the four required schedules (`DAILY_OPERATIONAL_SUMMARY`, `DAILY_CASH_CLOSING_EXCEPTIONS`, `WEEKLY_OPERATIONAL_SUMMARY`, `MONTHLY_OPERATIONAL_SUMMARY`) are all triggerable via `generate_snapshot()` from a CLI command or a real OS scheduled task the operator sets up outside this codebase, matching `reports.regenerate_daily`'s already-existing manual-trigger shape from Phase 9.5A.

## Canonical key and immutability

`(report_type, scope, period_start, period_end, currency, definition_version, snapshot_version)` — enforced as a real DB unique constraint (`uq_report_snapshot_canonical_key`), not just an application check. A `PUBLISHED` row's `payload` is never mutated after insert. `regenerate_snapshot()` always inserts a new row at `snapshot_version + 1` and flips the previous row to `status=SUPERSEDED` with `superseded_by_snapshot_id` set — the old row's content is untouched, only a status pointer is added.

## Concurrency

Two layers, matching the reasoning behind every other concurrency-safe operation in this codebase:
1. `pg_advisory_xact_lock()` keyed by a SHA-256 hash of the canonical key, serializing concurrent generation attempts for the *same* key (auto-released at transaction end).
2. The real unique constraint as the structural backstop — if a race somehow still produces two concurrent inserts, the loser's `IntegrityError` is caught and it re-reads the winner's row instead of raising.

`generate_snapshot()` is fully idempotent: calling it again for an already-`PUBLISHED` key returns the existing row without recomputing or duplicating.

## No outbound FK (extends the 9.5A DailyActivitySnapshot rule)

`ReportSnapshot` has zero `ForeignKey` columns — `generated_by_staff_user_id` and `superseded_by_snapshot_id` are plain UUIDs, matching `DailyActivitySnapshot`'s own "no FK into any other table" design so Reporting never becomes something another subsystem structurally depends on.

## Currency separation

Every operational-summary report type takes exactly one `currency` per call; a deployment with multiple active currencies gets one snapshot row per currency for the same period, never a blended total. `DAILY_CASH_CLOSING_EXCEPTIONS` is currency-agnostic (`currency=None`) since it reports closing *records*, not monetary totals.

## Timezone

`app/operational_reports/periods.py` computes all "previous completed" period boundaries in `Asia/Amman` (stdlib `zoneinfo`, no new dependency), matching `DailyActivitySnapshot.timezone`'s existing default from Phase 9.5A.
