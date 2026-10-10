"""`flask crash-reports prune` -- the 90-day retention for owner_crash_reports.

Same convention as `flask sync prune-events` / `flask reports generate-
scheduled` (app/cli.py): meant to be run by an external scheduler, refuses to
run unless OWNER_SCHEDULER_ROLE is explicitly "owner", and is a dry run unless
--apply is passed.

deploy/oracle-free/run_scheduled_ops.sh runs `crash-reports prune --apply`
nightly (added 2026-10-10; before that nothing called it and the table was
bounded only in practice -- at most one row per (installation, bug, build)).

This lives in the crash_reports package and is registered from app/__init__.py
rather than added to app/cli.py: a new command group needs no edit to the
shared CLI module.
"""
from __future__ import annotations

import json

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group("crash-reports")
    def crash_reports_group():
        """Crash-report maintenance jobs (retention)."""

    @crash_reports_group.command("prune")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually delete. Default is dry-run (report only).")
    @click.option("--days", default=None, type=int, help="Override the retention window in days (default 90).")
    def prune_cmd(apply_: bool, days: int | None):
        """Deletes crash reports whose last_seen_at is older than the
        retention window (90 days)."""
        from app.crash_reports.intake import RETENTION_DAYS, prune_old_reports
        from app.scheduling import SchedulerNotOwnerError, require_scheduler_owner

        try:
            require_scheduler_owner(app.config)
        except SchedulerNotOwnerError as exc:
            raise click.ClickException(str(exc)) from exc

        window = days if days else RETENTION_DAYS
        matched = prune_old_reports(days=window, dry_run=not apply_)
        click.echo(json.dumps({"dry_run": not apply_, "retention_days": window, "matched": matched}, indent=2))
